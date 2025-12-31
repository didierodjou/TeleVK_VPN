# --- START OF FILE packet_handler.py ---
import asyncio
import socket
import struct
from config import config
from telegram_transport import TelegramBotTransport
from vk_transport import VKTransport
from real_tap_interface import RealTapInterface
from network_manager import network_manager


class PacketHandler:
    def __init__(self):
        self.tap_interface = RealTapInterface()

        # Выбор транспорта
        if config.transport_type == 'vk':
            self.transport = VKTransport()
        else:
            self.transport = TelegramBotTransport()

        self.is_running = False
        self.mode = None
        self.my_mac = None
        self.peer_mac = b'\x02\x00\x00\x00\x00\x01'
        self.blocked_ips = {'255.255.255.255', '224.0.0.251', '224.0.0.252', '239.255.255.250'}
        self.blocked_ports = {137, 138, 139, 445, 1900, 5353, 5355}

    async def initialize(self, mode: str):
        self.mode = mode

        print("🧹 Pre-start network cleanup...")
        await network_manager.cleanup(config.tap_interface_name)
        ip = config.get_ip_for_mode(mode)

        if not await self.transport.initialize(self._handle_transport_packet, mode=mode):
            return False

        if not self.tap_interface.find_tap_interface():
            return False

        self.tap_interface.set_ip_address(ip, config.netmask)
        if not self.tap_interface.open_tap_device():
            return False

        self.my_mac = self.tap_interface.get_mac_address() or b'\x00\xff\x00\xff\x00\xff'

        if mode == 'client':
            await network_manager.setup_client_network(config.server_ip, self.tap_interface.interface_name)
        else:
            await network_manager.setup_server_network(self.tap_interface.interface_name)

        self.is_running = True
        return True

    async def start_reading_packets(self):
        await self.tap_interface.read_packets(self._handle_tap_packet)

    def _is_garbage(self, packet: bytes) -> bool:
        if len(packet) < 14: return True
        eth_type = packet[12:14]
        if eth_type == b'\x08\x06': return False
        if eth_type != b'\x08\x00': return True

        ip_header = packet[14:34]
        dst_ip = socket.inet_ntoa(ip_header[16:20])
        if dst_ip in self.blocked_ips or dst_ip.startswith("224.") or dst_ip.endswith(".255"):
            return True

        protocol = ip_header[9]
        if protocol == 17:  # UDP
            ihl = (ip_header[0] & 0x0F) * 4
            udp_start = 14 + ihl
            dst_port = struct.unpack('!H', packet[udp_start + 2:udp_start + 4])[0]
            if dst_port in self.blocked_ports: return True
        return False

    async def _handle_tap_packet(self, packet: bytes):
        if not self.is_running or self._is_garbage(packet): return
        eth_type = packet[12:14]

        if eth_type == b'\x08\x06':
            await self._handle_arp(packet)
        elif eth_type == b'\x08\x00':
            await self.transport.send_data(packet[14:])

    async def _handle_transport_packet(self, ip_packet: bytes):
        if not self.is_running: return
        eth = self.my_mac + self.peer_mac + b'\x08\x00'
        await self.tap_interface.write_packet(eth + ip_packet)

    async def _handle_arp(self, packet: bytes):
        try:
            arp_body = packet[14:]
            if arp_body[6:8] == b'\x00\x01':
                t_ip = socket.inet_ntoa(arp_body[24:28])
                should_reply = (self.mode == 'client' and t_ip == config.server_ip) or \
                               (self.mode == 'server' and t_ip == config.client_ip)
                if should_reply:
                    req_mac = packet[6:12]
                    reply = (req_mac + self.peer_mac + b'\x08\x06' +
                             b'\x00\x01\x08\x00\x06\x04\x00\x02' +
                             self.peer_mac + arp_body[24:28] +
                             req_mac + arp_body[14:18])
                    await self.tap_interface.write_packet(reply)
        except:
            pass

    async def shutdown(self):
        self.is_running = False
        await self.transport.disconnect()
        if self.tap_interface.interface_name:
            await network_manager.cleanup(self.tap_interface.interface_name)