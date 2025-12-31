# --- START OF FILE vk_transport.py ---
import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
from vk_api.upload import VkUpload
from vk_api.exceptions import Captcha, ApiError  # <--- ВАЖНО
import asyncio
import io
import time
import requests
from typing import Callable, Optional
from concurrent.futures import ThreadPoolExecutor

from config import config
from crypto_utils import CryptoManager
from compressor import Compressor


class VKTransport:
    def __init__(self):
        self.vk_session = None
        self.vk = None
        self.upload = None
        self.longpoll = None

        self.receive_callback = None
        self.crypto = CryptoManager(config.encryption_key)
        self.compressor = Compressor()
        self.is_connected = False

        self.send_queue = asyncio.Queue()
        self.sender_task = None
        self.receiver_task = None
        # Уменьшаем кол-во потоков до 1, чтобы капчи вылетали по очереди, а не пачкой
        self.upload_semaphore = asyncio.Semaphore(1)

        self.captcha_callback: Optional[Callable] = None
        self.two_factor_callback: Optional[Callable] = None
        self.executor = ThreadPoolExecutor(max_workers=2)

    def _captcha_handler(self, captcha):
        """Автоматический обработчик (для служебных запросов)"""
        print(f"⚠️ CAPTCHA DETECTED (Auto): {captcha.get_url()}")
        if self.captcha_callback:
            key = self.captcha_callback(captcha.get_url())
            if key:
                return captcha.try_again(key)
        print("❌ Captcha skipped")
        return None

    def _2fa_handler(self):
        print("🔐 VK 2FA Requested...")
        if self.two_factor_callback:
            code = self.two_factor_callback()
            if code: return code, True
        return None, True

    async def initialize(self, receive_callback: Callable, mode: str = 'server'):
        self.receive_callback = receive_callback

        try:
            print(f"🔷 VK Connecting ({mode.upper()})...")

            if config.vk_token and len(config.vk_token) > 10:
                print("🔑 Using Access Token")
                self.vk_session = vk_api.VkApi(token=config.vk_token)
                try:
                    self.vk = self.vk_session.get_api()
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(self.executor, lambda: self.vk.users.get())
                except Exception as e:
                    print(f"❌ Token Invalid: {e}")
                    return False
            else:
                print("👤 Using Login/Password")
                self.vk_session = vk_api.VkApi(
                    login=config.vk_login,
                    password=config.vk_password,
                    app_id=config.vk_app_id,
                    captcha_handler=self._captcha_handler,
                    auth_handler=self._2fa_handler
                )
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(self.executor, self.vk_session.auth)
                self.vk = self.vk_session.get_api()

            self.upload = VkUpload(self.vk_session)
            self.longpoll = VkLongPoll(self.vk_session)

            print(f"✅ VK Connected. Peer: {config.vk_peer_id}")
            self.is_connected = True

            self.sender_task = asyncio.create_task(self._batch_sender_worker())
            self.receiver_task = asyncio.create_task(self._receiver_worker())
            return True

        except Exception as e:
            print(f"❌ VK Init Error: {e}")
            return False

    async def send_data(self, data: bytes):
        if not self.is_connected: return
        # Ограничиваем очередь, чтобы при капче память не забилась
        if self.send_queue.qsize() > 500:
            try:
                self.send_queue.get_nowait()
            except:
                pass
        await self.send_queue.put(data)

    async def _batch_sender_worker(self):
        print("📦 VK Sender Started")
        buffer = bytearray()
        while self.is_connected:
            try:
                packet = await self.send_queue.get()
                self._append_to_buffer(buffer, packet)
                self.send_queue.task_done()

                start = time.time()
                while len(buffer) < config.max_batch_size:
                    rem = config.batch_interval - (time.time() - start)
                    if rem <= 0: break
                    try:
                        packet = await asyncio.wait_for(self.send_queue.get(), timeout=rem)
                        self._append_to_buffer(buffer, packet)
                        self.send_queue.task_done()
                    except asyncio.TimeoutError:
                        break

                if buffer:
                    data = bytes(buffer)
                    buffer.clear()
                    # Запускаем отправку
                    asyncio.create_task(self._send_batch_task(data))

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Sender Error: {e}")
                await asyncio.sleep(0.1)

    def _append_to_buffer(self, buffer, packet):
        buffer.extend(len(packet).to_bytes(2, 'big'))
        buffer.extend(packet)

    async def _send_batch_task(self, raw_data: bytes):
        async with self.upload_semaphore:
            try:
                data = self.compressor.compress(raw_data) if config.compression_enabled else raw_data
                enc_data = self.crypto.encrypt(data)

                # Создаем новый буфер для каждой попытки (чтобы seek(0) работал корректно)
                f_data = enc_data

                loop = asyncio.get_running_loop()
                await loop.run_in_executor(self.executor, self._blocking_send, f_data)
            except Exception as e:
                # print(f"⚠️ Send Fail: {e}") # Отключаем спам в лог
                pass

    def _blocking_send(self, data_bytes):
        """Блокирующая отправка с ручной обработкой капчи"""
        retries = 0
        max_retries = 5

        while retries < max_retries:
            try:
                # Подготовка файла
                f = io.BytesIO(data_bytes)
                f.name = "d.bin"

                # 1. Загрузка
                doc = self.upload.document_message(f, peer_id=int(config.vk_peer_id))
                d = doc['doc']

                # 2. Отправка
                self.vk.messages.send(
                    peer_id=int(config.vk_peer_id),
                    attachment=f"doc{d['owner_id']}_{d['id']}",
                    random_id=0
                )
                return  # Успех

            except Captcha as e:
                print(f"⚠️ SEND CAPTCHA: {e.get_url()}")
                if self.captcha_callback:
                    # Вызываем GUI и ждем ввода
                    code = self.captcha_callback(e.get_url())
                    if code:
                        e.try_again(code)  # Обновляем параметры капчи для следующей попытки
                        print(f"✅ Retry with code: {code}")
                        continue  # Повторяем цикл while
                print("❌ Captcha not solved, dropping packet")
                break

            except ApiError as e:
                if e.code == 9:  # Flood control
                    print("⏳ Flood limit. Sleeping 1s...")
                    time.sleep(1)
                    retries += 1
                elif e.code == 14:  # Captcha needed (иногда прилетает как ApiError)
                    # Обработка сложнее, vk_api обычно само кидает Captcha exception
                    print(f"⚠️ API Error 14 (Captcha): {e}")
                    break
                else:
                    print(f"❌ API Error: {e}")
                    break
            except Exception as e:
                print(f"❌ Unknown Send Error: {e}")
                break

    async def _receiver_worker(self):
        print("📥 VK Receiver Started")
        loop = asyncio.get_running_loop()
        while self.is_connected:
            try:
                events = await loop.run_in_executor(self.executor, lambda: list(self.longpoll.check()))
                for event in events:
                    if event.type == VkEventType.MESSAGE_NEW and event.to_me and event.peer_id == int(
                            config.vk_peer_id):
                        if event.attachments.get('attach1_type') == 'doc':
                            asyncio.create_task(self._process_msg(event.message_id))
            except Exception as e:
                # print(f"Receiver Error: {e}")
                await asyncio.sleep(1)

    async def _process_msg(self, mid):
        try:
            loop = asyncio.get_running_loop()
            res = await loop.run_in_executor(self.executor, lambda: self.vk.messages.getById(message_ids=[mid]))
            if not res['items']: return
            for att in res['items'][0].get('attachments', []):
                if att['type'] == 'doc':
                    url = att['doc']['url']
                    content = await loop.run_in_executor(None, lambda: requests.get(url).content)
                    try:
                        dec = self.crypto.decrypt(content)
                        data = self.compressor.decompress(dec) if config.compression_enabled else dec
                        await self._route_data(data)
                    except:
                        pass
        except Exception as e:
            pass

    async def _route_data(self, data):
        idx = 0
        l = len(data)
        while idx < l:
            if idx + 2 > l: break
            pl = int.from_bytes(data[idx:idx + 2], 'big')
            idx += 2
            if idx + pl > l: break
            if self.receive_callback:
                await self.receive_callback(data[idx:idx + pl])
            idx += pl

    async def disconnect(self):
        self.is_connected = False
        if self.sender_task: self.sender_task.cancel()
        if self.receiver_task: self.receiver_task.cancel()
        self.executor.shutdown(wait=False)