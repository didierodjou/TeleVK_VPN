# --- START OF FILE main.py ---
import argparse
import asyncio
import signal
import sys
import logging

try:
    from proxy_handler import ProxyHandler as CurrentHandler
    HANDLER_TYPE = "PROXY"
except ImportError:
    from packet_handler import PacketHandler as CurrentHandler
    HANDLER_TYPE = "VPN"

from config import config

logger = logging.getLogger("VPN_Core")
logger.setLevel(logging.INFO)


class VPNApplication:
    def __init__(self):
        self.handler = CurrentHandler()
        self.is_running = False
        self.mode = None
        self.traffic_started = False
        self.traffic_callback = None

        self.auth_phone_callback = None
        self.auth_code_callback = None
        self.auth_pass_callback = None

    def set_callbacks(self, on_traffic=None, auth_phone=None, auth_code=None, auth_pass=None):
        self.traffic_callback = on_traffic
        self.auth_phone_callback = auth_phone
        self.auth_code_callback = auth_code
        self.auth_pass_callback = auth_pass

    async def initialize(self, mode: str):
        self.mode = mode
        logger.info(f"Инициализация: {mode.upper()} [{config.transport_type.upper()}]")

        if hasattr(self.handler, 'transport'):
            t = self.handler.transport
            if config.transport_type == 'telegram':
                t.phone_callback = self.auth_phone_callback
                t.code_callback = self.auth_code_callback
                t.password_callback = self.auth_pass_callback
            elif config.transport_type == 'vk':
                # Для ВК auth_code_callback используется и для капчи, и для 2FA
                t.captcha_callback = self.auth_code_callback
                t.two_factor_callback = self.auth_code_callback # <--- ВАЖНОЕ ДОБАВЛЕНИЕ

        success = await self.handler.initialize(mode)
        if not success:
            logger.error("❌ Ошибка инициализации Handler")
            return False

        # Хуки трафика
        if mode == 'client':
            orig = self.handler._handle_tap_packet

            async def wrapped(pkt):
                await orig(pkt)
                if not self.traffic_started and not self.handler._is_garbage(pkt):
                    self.traffic_started = True
                    if self.traffic_callback: self.traffic_callback()

            self.handler._handle_tap_packet = wrapped

        elif mode == 'server':
            t = self.handler.transport
            orig_recv = t.receive_callback

            async def wrapped_recv(pkt):
                if orig_recv: await orig_recv(pkt)
                if not self.traffic_started:
                    self.traffic_started = True
                    if self.traffic_callback: self.traffic_callback()

            t.receive_callback = wrapped_recv

        self.is_running = True
        return True

    async def run_async(self, mode: str):
        if not await self.initialize(mode): return
        try:
            await self.handler.start_reading_packets()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Fatal: {e}")
        finally:
            await self.shutdown()

    async def shutdown(self):
        logger.info("🛑 Остановка...")
        if hasattr(self.handler, 'shutdown'):
            await self.handler.shutdown()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    parser = argparse.ArgumentParser()
    parser.add_argument('mode', nargs='?', choices=['server', 'client'])
    args = parser.parse_args()

    app = VPNApplication()
    signal.signal(signal.SIGINT, lambda s, f: asyncio.create_task(app.shutdown()))

    if args.mode:
        asyncio.run(app.run_async(args.mode))