# --- START OF FILE real_tap_interface.py ---

import os
import asyncio
import subprocess
import ctypes
from ctypes import wintypes


class RealTapInterface:
    """Работа с TAP-Windows6 напрямую через \\\\.\\Global\\{GUID}.tap"""

    def __init__(self):
        self.tap_handle = None
        self.interface_guid = None
        self.interface_name = None
        self.local_ip = None
        self.buffer_size = 65535
        self.is_running = False
        self.packet_count = 0

    # === 1. Поиск TAP интерфейса ===
    def find_tap_interface(self) -> bool:
        """Находит TAP-интерфейс и GUID через PowerShell"""
        try:
            ps_script = '''
            $tap = Get-NetAdapter | Where-Object {$_.InterfaceDescription -like "*TAP*"} | Select-Object -First 1
            if ($tap) {
                Write-Host $tap.Name
                $key = "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e972-e325-11ce-bfc1-08002be10318}"
                $guid = (Get-ChildItem $key | Get-ItemProperty | Where-Object { $_.NetCfgInstanceId -eq $tap.InterfaceGuid }).NetCfgInstanceId
                Write-Host $guid
            }
            '''
            result = subprocess.run(
                ['powershell', '-Command', ps_script],
                capture_output=True, text=True, check=True
            )

            lines = result.stdout.strip().split('\n')
            if len(lines) >= 2:
                self.interface_name = lines[0].strip()
                self.interface_guid = lines[1].strip()
                print(f"✅ Found TAP: {self.interface_name}, GUID: {self.interface_guid}")
                return True
            else:
                print("❌ No TAP interface found.")
                return False
        except Exception as e:
            print(f"❌ Error finding TAP interface: {e}")
            return False

    # === 2. Настройка IP ===
    def set_ip_address(self, ip: str, netmask: str = "255.255.255.0") -> bool:
        """Настройка IP-адреса TAP-интерфейса"""
        try:
            if not self.interface_name:
                if not self.find_tap_interface():
                    return False

            ps_script = f'''
            Remove-NetIPAddress -InterfaceAlias "{self.interface_name}" -Confirm:$false -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds 500
            New-NetIPAddress -IPAddress {ip} -PrefixLength 24 -InterfaceAlias "{self.interface_name}"
            Enable-NetAdapter -Name "{self.interface_name}" -Confirm:$false
            '''
            subprocess.run(['powershell', '-Command', ps_script], capture_output=True, check=True)
            self.local_ip = ip
            print(f"✅ IP {ip} set for {self.interface_name}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to set IP: {e}")
            return False

    # === 3. Получение MAC (НОВОЕ) ===
    def get_mac_address(self) -> bytes:
        """Получает MAC адрес интерфейса в байтах"""
        if not self.interface_name:
            return None
        try:
            cmd = f'powershell -Command "(Get-NetAdapter -Name \'{self.interface_name}\').MacAddress"'
            res = subprocess.run(cmd, capture_output=True, text=True, shell=True)
            # Формат возврата: 00-FF-AA... -> байты
            mac_str = res.stdout.strip().replace('-', '').replace(':', '')
            if len(mac_str) == 12:
                return bytes.fromhex(mac_str)
        except Exception as e:
            print(f"❌ Error getting MAC: {e}")
        return None

    # === 4. Открытие TAP устройства ===
    def open_tap_device(self) -> bool:
        """Открывает TAP устройство через CreateFileW"""
        try:
            if not self.interface_guid:
                if not self.find_tap_interface():
                    return False

            device_path = f"\\\\.\\Global\\{self.interface_guid}.tap"
            print(f"🔧 Opening TAP device: {device_path}")

            # Константы для CreateFile
            GENERIC_READ = 0x80000000
            GENERIC_WRITE = 0x40000000
            OPEN_EXISTING = 3
            FILE_ATTRIBUTE_SYSTEM = 0x4

            CreateFile = ctypes.windll.kernel32.CreateFileW
            CreateFile.restype = wintypes.HANDLE
            CreateFile.argtypes = [
                wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE
            ]

            handle = CreateFile(
                device_path,
                GENERIC_READ | GENERIC_WRITE,
                0, None,
                OPEN_EXISTING,
                FILE_ATTRIBUTE_SYSTEM,
                None
            )

            if handle == wintypes.HANDLE(-1).value or handle == 0:
                raise PermissionError("❌ Unable to open TAP device (driver access denied).")

            self.tap_handle = handle
            print("✅ TAP handle created successfully")

            # Устанавливаем статус “connected”
            TAP_IOCTL_SET_MEDIA_STATUS = 0x22C084
            status = ctypes.c_ulong(1)
            bytes_returned = wintypes.DWORD()
            ctypes.windll.kernel32.DeviceIoControl(
                handle,
                TAP_IOCTL_SET_MEDIA_STATUS,
                ctypes.byref(status),
                ctypes.sizeof(status),
                None,
                0,
                ctypes.byref(bytes_returned),
                None
            )

            print("✅ TAP interface set to connected state")
            return True

        except Exception as e:
            print(f"❌ Failed to open TAP device: {e}")
            return False

    # === 5. Чтение пакетов ===
    async def read_packets(self, packet_handler):
        """Чтение пакетов напрямую через ReadFile"""
        if not self.tap_handle:
            print("❌ TAP handle not initialized")
            return

        self.is_running = True
        print("🚀 TAP packet reader started...")

        loop = asyncio.get_event_loop()
        while self.is_running:
            try:
                data = await loop.run_in_executor(None, self._read_from_tap)
                if data:
                    self.packet_count += 1
                    await packet_handler(data)
            except Exception as e:
                print(f"❌ Error reading TAP: {e}")
                await asyncio.sleep(0.05)

    def _read_from_tap(self) -> bytes:
        """Блокирующее чтение TAP"""
        buffer = ctypes.create_string_buffer(self.buffer_size)
        bytes_read = wintypes.DWORD()
        success = ctypes.windll.kernel32.ReadFile(
            self.tap_handle,
            buffer,
            self.buffer_size,
            ctypes.byref(bytes_read),
            None
        )
        if not success or bytes_read.value == 0:
            return b''
        return buffer.raw[:bytes_read.value]

    # === 6. Запись пакета ===
    async def write_packet(self, packet: bytes) -> bool:
        """Запись пакета через WriteFile"""
        try:
            if not self.tap_handle:
                return False

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: self._write_to_tap(packet))
            return True
        except Exception as e:
            print(f"❌ Error writing TAP packet: {e}")
            return False

    def _write_to_tap(self, packet: bytes):
        buffer = ctypes.create_string_buffer(packet)
        bytes_written = wintypes.DWORD()
        ctypes.windll.kernel32.WriteFile(
            self.tap_handle,
            buffer,
            len(packet),
            ctypes.byref(bytes_written),
            None
        )

    # === 7. Закрытие ===
    def close(self):
        """Закрывает TAP"""
        self.is_running = False
        if self.tap_handle:
            ctypes.windll.kernel32.CloseHandle(self.tap_handle)
            self.tap_handle = None
            print("✅ TAP device closed")