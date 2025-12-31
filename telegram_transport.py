# --- START OF FILE telegram_transport.py ---
from telethon import TelegramClient, events
import asyncio
import time
import io
import logging
from typing import Callable, Optional
from config import config
from crypto_utils import CryptoManager
from compressor import Compressor

# Настройка логгера (чтобы видеть ошибки в консоли GUI)
logger = logging.getLogger("VPN_Core")


class TelegramBotTransport:
    # --- ВАЖНО: Статические переменные ---
    # Они заполняются из main.py ДО создания экземпляра класса.
    # Не удаляйте и не переносите их в __init__.
    phone_callback: Optional[Callable] = None
    code_callback: Optional[Callable] = None
    password_callback: Optional[Callable] = None

    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self.receive_callback = None
        self.crypto = CryptoManager(config.encryption_key)
        self.compressor = Compressor()
        self.is_connected = False
        self.chat_entity = None
        self.send_queue = asyncio.Queue()
        self.sender_task = None
        self.me = None
        self.upload_semaphore = asyncio.Semaphore(5)
        # УБРАНО обнуление callbacks здесь, чтобы использовались статические переменные

    async def initialize(self, receive_callback: Callable, mode: str = 'server'):
        self.receive_callback = receive_callback
        is_client = (mode == 'client')

        try:
            print(f"🔗 Telegram Connecting ({mode.upper()})...")
            session_name = 'vpn_client_session' if is_client else 'vpn_server_session'

            self.client = TelegramClient(session_name, config.api_id, config.api_hash)
            self.client.flood_sleep_threshold = 24 * 60 * 60

            if is_client:
                print("👤 Logging in as USER (Interactive)...")

                # Проверка, что колбэки передались
                if not self.phone_callback:
                    print("⚠️ WARNING: Phone callback is missing! GUI might hang.")

                # Используем self.phone_callback (который берется из класса)
                await self.client.start(
                    phone=self.phone_callback,
                    code_callback=self.code_callback,
                    password=self.password_callback
                )
            else:
                print("🤖 Logging in as BOT...")
                await self.client.start(bot_token=config.bot_token)

            self.me = await self.client.get_me()
            self.is_connected = True

            username = self.me.username if self.me.username else self.me.first_name
            print(f"✅ Logged in as: {username} (ID: {self.me.id})")

            await self._setup_chat()

            self.sender_task = asyncio.create_task(self._batch_sender_worker())

            @self.client.on(events.NewMessage(chats=self.chat_entity))
            async def handler(event):
                if event.sender_id == self.me.id: return
                asyncio.create_task(self._handle_new_message(event))

            return True
        except Exception as e:
            print(f"❌ Telegram Init Error: {e}")
            import traceback
            traceback.print_exc()  # Печатаем полный лог ошибки
            return False

    async def _setup_chat(self):
        try:
            self.chat_entity = await self.client.get_entity(config.chat_id)
            print(f"✅ Tunnel Endpoint: {config.chat_id}")
        except Exception as e:
            print(f"⚠️ Error getting chat entity: {e}")
            raise e

    async def send_data(self, data: bytes):
        if not self.is_connected: return
        if self.send_queue.qsize() > 5000:
            try:
                self.send_queue.get_nowait()
            except:
                pass
        await self.send_queue.put(data)

    async def _batch_sender_worker(self):
        print("📦 Batch sender started")
        buffer = bytearray()

        while self.is_connected:
            try:
                packet = await self.send_queue.get()
                self._append_to_buffer(buffer, packet)
                self.send_queue.task_done()

                start_time = time.time()
                while len(buffer) < config.max_batch_size:
                    elapsed = time.time() - start_time
                    remaining = config.batch_interval - elapsed
                    if remaining <= 0: break
                    try:
                        packet = await asyncio.wait_for(self.send_queue.get(), timeout=remaining)
                        self._append_to_buffer(buffer, packet)
                        self.send_queue.task_done()
                    except asyncio.TimeoutError:
                        break

                if buffer:
                    data_to_send = bytes(buffer)
                    buffer.clear()
                    asyncio.create_task(self._send_batch_task(data_to_send))

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ Worker Error: {e}")
                buffer.clear()
                await asyncio.sleep(0.1)

    def _append_to_buffer(self, buffer: bytearray, packet: bytes):
        length = len(packet)
        buffer.extend(length.to_bytes(2, 'big'))
        buffer.extend(packet)

    async def _send_batch_task(self, raw_data: bytes):
        async with self.upload_semaphore:
            try:
                if config.compression_enabled:
                    data_to_send = self.compressor.compress(raw_data)
                else:
                    data_to_send = raw_data

                encrypted_data = self.crypto.encrypt(data_to_send)

                # Лог отправки (можно закомментировать, если спамит)
                size_kb = len(encrypted_data) / 1024
                # print(f"📤 UP: {size_kb:.1f} KB")

                file_obj = io.BytesIO(encrypted_data)
                file_obj.name = "d"

                await self.client.send_file(
                    self.chat_entity,
                    file_obj,
                    force_document=True,
                    allow_cache=False,
                    attributes=[]
                )
            except Exception as e:
                print(f"⚠️ Send Error: {e}")

    async def _handle_new_message(self, event):
        try:
            if not event.message.file: return

            encrypted_data = await event.message.download_media(file=bytes)
            if not encrypted_data: return

            # Лог приема (можно закомментировать)
            # size_kb = len(encrypted_data) / 1024
            # print(f"📥 DOWN: {size_kb:.1f} KB")

            try:
                decrypted_data = self.crypto.decrypt(encrypted_data)
            except:
                return

            if config.compression_enabled:
                try:
                    batch_data = self.compressor.decompress(decrypted_data)
                except:
                    print("⚠️ Decompression failed")
                    return
            else:
                batch_data = decrypted_data

            await self._parse_batch_and_route(batch_data)
        except Exception as e:
            print(f"❌ Recv Error: {e}")

    async def _parse_batch_and_route(self, data: bytes):
        idx = 0
        total_len = len(data)
        while idx < total_len:
            if idx + 2 > total_len: break
            pkt_len = int.from_bytes(data[idx:idx + 2], 'big')
            idx += 2
            if idx + pkt_len > total_len: break
            packet = data[idx:idx + pkt_len]
            idx += pkt_len

            if self.receive_callback:
                await self.receive_callback(packet)

    async def disconnect(self):
        self.is_connected = False
        if self.sender_task: self.sender_task.cancel()
        if self.client: await self.client.disconnect()