# --- START OF FILE config.py ---
import json
import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class VPNConfig:
    # --- ВЫБОР ТРАНСПОРТА ---
    # 'telegram' или 'vk'
    transport_type: str = 'telegram'

    # --- TELEGRAM CONFIG ---
    api_id: int = 25889955
    api_hash: str = '95f1b60c26d67cd2381efe476098d66b'
    bot_token: str = '8400761477:AAH5QKx5sYOwuUEIXDo5mTYxm-Y6yOV_ahc'
    chat_id: str = '@mychannelvpn'

    # --- VK CONFIG (НОВОЕ) ---
    vk_login: str = '+79964382061'  # Номер телефона (например, +79001234567)
    vk_token: str = 'vk1.a.sG88tcH_T4MvzhkAczctS1qfe6cannNsYGJ4T2nUEiTC1wXflx5rfmKke9ekT_GKZ3OSLnwR8zJfbfrvb6FOWhyE-xjYSbGPhGr9U-iU3duWJDWYAX-_7QcmHfiutvh2OMVEjf37hDnYCEClHm7L5H1cAcGlIvNSKZ31NhXvs_VBm5cyj-cSEMlc1Qx9x21fxdWqneq9KMI-sYLJpfI8DA'  # Пароль от аккаунта
    vk_peer_id: str = '589972849'  # ID собеседника (Server <-> Client)
    vk_app_id: int = 828835938  # ID приложения (Admin VK)6121396

    # --- СЕТЕВЫЕ НАСТРОЙКИ ---
    tap_interface_name: str = 'Ethernet 5'
    server_ip: str = '10.8.0.1'
    client_ip: str = '10.8.0.2'
    netmask: str = '255.255.255.0'

    location_label: str = 'Unknown PC'

    # Оптимальный MTU (чуть меньше стандартного из-за оверхеда протоколов)
    mtu: int = 1280

    subnet: str = '10.8.0.0'
    subnet_mask: str = '255.255.255.0'
    encryption_key: str = 'U&U?OglmE4P;0.32_Ktliw>uP]%PL:&d'

    # Сжатие (True экономит трафик, False уменьшает пинг)
    compression_enabled: bool = False

    # Настройки пакетирования
    batch_interval: float = 0.05
    max_batch_size: int = 512 * 1024

    # --- СПИСОК ИСКЛЮЧЕНИЙ (IP, которые идут мимо VPN) ---
    # Включает подсети Telegram и VKontakte/Mail.ru
    telegram_subnets: List[str] = field(default_factory=lambda: [
        # === TELEGRAM NETWORKS ===
        "91.108.4.0/22",
        "91.108.8.0/22",
        "91.108.12.0/22",
        "91.108.16.0/22",
        "91.108.56.0/22",
        "149.154.160.0/20",
        "149.154.164.0/22",
        "149.154.168.0/22",
        "149.154.172.0/22",

        # === VKONTAKTE (VK & Mail.ru Group) NETWORKS ===
        # Основная подсеть VK
        "87.240.128.0/18",
        # Инфраструктура и дата-центры
        "93.186.224.0/20",
        "95.142.192.0/20",
        # CDN, Медиа и прочие сервисы Mail.ru/VK
        "185.32.248.0/22",
        "188.93.56.0/24",
        "128.140.168.0/21",
        "195.218.169.0/24",
        "79.137.183.0/24"
    ])

    def get_ip_for_mode(self, mode: str) -> str:
        return self.server_ip if mode == "server" else self.client_ip

    @classmethod
    def load_from_file(cls, filename: str = 'config.json'):
        if os.path.exists(filename):
            try:
                with open(filename, 'r') as f:
                    data = json.load(f)
                # Фильтруем ключи, чтобы брать только те, что есть в классе
                valid_keys = {k: v for k, v in data.items() if k in cls.__annotations__}
                return cls(**valid_keys)
            except:
                pass
        return cls()

    def save_to_file(self, filename: str = 'config.json'):
        with open(filename, 'w') as f:
            json.dump(self.__dict__, f, indent=4)


# Загружаем конфиг сразу при импорте
config = VPNConfig.load_from_file()
