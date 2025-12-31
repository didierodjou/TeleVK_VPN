# --- START OF FILE network_manager.py ---
import subprocess
import asyncio
import socket
from config import config


class NetworkManager:
    def _run_ps(self, cmd):
        full_cmd = f'powershell -NoProfile -ExecutionPolicy Bypass -Command "{cmd}"'
        try:
            subprocess.run(full_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"Error executing PS: {cmd} -> {e}")

    def _get_interface_index(self, name):
        cmd = f'powershell -Command "(Get-NetAdapter -Name \'{name}\').InterfaceIndex"'
        res = subprocess.run(cmd, capture_output=True, text=True, shell=True)
        if res.returncode == 0 and res.stdout.strip().isdigit():
            return res.stdout.strip()
        return None

    def _set_mtu(self, if_index):
        self._run_ps(f"Set-NetIPInterface -InterfaceIndex {if_index} -NlMtuBytes {config.mtu}")

    def _get_default_gateway(self):
        try:
            cmd = "route print 0.0.0.0"
            res = subprocess.run(cmd, capture_output=True, text=True, shell=True)
            for line in res.stdout.splitlines():
                if "0.0.0.0" in line and "0.0.0.0" in line[10:]:
                    parts = line.split()
                    if len(parts) >= 5:
                        gw = parts[2]
                        if not gw.startswith("10.8."):
                            return gw
        except:
            pass
        return None

    def _configure_firewall(self, interface_name):
        self._run_ps(f'Set-NetConnectionProfile -InterfaceAlias "{interface_name}" -NetworkCategory Private')
        self._run_ps(
            f'New-NetFirewallRule -DisplayName "VPN_IN" -Direction Inbound -InterfaceAlias "{interface_name}" -Action Allow -Enabled True')
        self._run_ps(
            f'New-NetFirewallRule -DisplayName "VPN_OUT" -Direction Outbound -InterfaceAlias "{interface_name}" -Action Allow -Enabled True')

    def _resolve_api_ips(self):
        """Резолвит IP адреса Telegram и VK для исключения из VPN"""
        # Telegram домены
        domains = ['api.telegram.org', 'telegram.org']

        # VK домены (API, Загрузка, LongPoll)
        if config.transport_type == 'vk':
            domains.extend([
                'api.vk.com', 'vk.com', 'im.vk.com', 'pu.vk.com', 'login.vk.com'
            ])

        ips = []
        for d in domains:
            try:
                # Получаем все IP для домена (IPv4)
                addr_info = socket.getaddrinfo(d, 443, family=socket.AF_INET, proto=socket.IPPROTO_TCP)
                for res in addr_info:
                    ip = res[4][0]
                    if ip not in ips:
                        ips.append(f"{ip}/32")
                        print(f"🌍 Resolved {d} -> {ip}")
            except:
                pass
        return list(set(ips))

    async def setup_client_network(self, vpn_server_ip, interface_name):
        print(f"🌐 Setting up Client Routing on {interface_name}...")

        gw = self._get_default_gateway()
        if not gw:
            print("⚠️ ERROR: Default Gateway not found!")
            return

        if_index = self._get_interface_index(interface_name)
        if not if_index:
            print(f"⚠️ Interface '{interface_name}' not found.")
            return

        self._set_mtu(if_index)

        # 1. Собираем все IP, которые НЕ должны идти через VPN
        # ИСПРАВЛЕНИЕ: config.telegram_subnets уже содержит все диапазоны (и ТГ, и ВК)
        routes_to_exclude = config.telegram_subnets + self._resolve_api_ips()

        print(f"🛡️ Excluding {len(routes_to_exclude)} routes (API & Subnets) from VPN...")

        for subnet in routes_to_exclude:
            # Metric 1 для реального шлюза (чтобы перебить VPN)
            self._run_ps(f"route add {subnet} {gw} metric 1")

        # 2. Заворачиваем ВЕСЬ ОСТАЛЬНОЙ трафик в VPN
        # (Разбиваем 0.0.0.0/0 на две части, чтобы перебить дефолтный маршрут, не удаляя его)
        self._run_ps(f"route add 0.0.0.0 mask 128.0.0.0 {vpn_server_ip} metric 1 IF {if_index}")
        self._run_ps(f"route add 128.0.0.0 mask 128.0.0.0 {vpn_server_ip} metric 1 IF {if_index}")

        # DNS
        self._run_ps(f"Set-DnsClientServerAddress -InterfaceIndex {if_index} -ServerAddresses ('8.8.8.8','1.1.1.1')")
        self._configure_firewall(interface_name)

    async def setup_server_network(self, interface_name):
        print(f"🌐 Setting up Server NAT on {interface_name}...")

        if_index = self._get_interface_index(interface_name)
        if if_index: self._set_mtu(if_index)

        subprocess.run(
            r"reg add HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters /v IPEnableRouter /t REG_DWORD /d 1 /f",
            shell=True, stdout=subprocess.DEVNULL)
        self._configure_firewall(interface_name)

        self._run_ps("Remove-NetNat -Name 'TelegramVPN_NAT' -Confirm:$false -ErrorAction SilentlyContinue")
        await asyncio.sleep(1)
        self._run_ps(f"New-NetNat -Name 'TelegramVPN_NAT' -InternalIPInterfaceAddressPrefix '{config.subnet}/24'")
        print("✅ Server NAT Configured")

    async def cleanup(self, interface_name):
        print("🧹 Cleaning up routes...")
        self._run_ps("Remove-NetNat -Name 'TelegramVPN_NAT' -Confirm:$false -ErrorAction SilentlyContinue")

        # Удаляем маршруты VPN
        self._run_ps("route delete 0.0.0.0 mask 128.0.0.0")
        self._run_ps("route delete 128.0.0.0 mask 128.0.0.0")

        # Удаляем исключения для API
        # ИСПРАВЛЕНИЕ: только telegram_subnets (там всё есть)
        routes_to_cleanup = config.telegram_subnets + self._resolve_api_ips()
        for subnet in routes_to_cleanup:
            base_ip = subnet.split('/')[0]
            self._run_ps(f"route delete {base_ip}")


network_manager = NetworkManager()