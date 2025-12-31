# --- START OF FILE crypto_utils.py ---

import base64
from typing import Optional

# Импорт паддинга из pycryptodome (универсальный PKCS7)
from Crypto.Util.Padding import pad, unpad
from Crypto.Random import get_random_bytes

# Попытка импорта GOST. Если нет - кидаем понятную ошибку.
try:
    from gostcrypto import gostcipher
except ImportError:
    raise ImportError(
        "Библиотека 'gostcrypto' не найдена. "
        "Установите её командой: pip install gostcrypto"
    )


class CryptoManager:
    """
    Менеджер шифрования, использующий ГОСТ Р 34.12-2015 (Кузнечик).
    Режим работы: CBC (Cipher Block Chaining).
    """

    def __init__(self, key: str):
        # Кузнечик использует 256-битный ключ (32 байта)
        if len(key) != 32:
            raise ValueError(f"Key for GOST (Kuznechik) must be 32 bytes long. Provided: {len(key)}")

        self.key = key.encode('utf-8')

        # Размер блока у Кузнечика — 128 бит (16 байт), как у AES.
        # У Магмы (Magma) — 64 бита (8 байт).
        self.block_size = 16

    def encrypt(self, data: bytes) -> bytes:
        """Шифрование данных (ГОСТ Кузнечик + IV + Padding)"""
        # Генерируем случайный вектор инициализации (IV) равный размеру блока
        iv = get_random_bytes(self.block_size)

        # Инициализация шифра: Алгоритм 'kuznechik', режим CBC
        cipher = gostcipher.new('kuznechik', self.key, gostcipher.MODE_CBC, init_vect=iv)

        # Добавляем стандартный PKCS7 паддинг
        padded_data = pad(data, self.block_size)

        # Шифруем
        encrypted_data = cipher.encrypt(padded_data)

        # Возвращаем IV + Шифротекст (IV нужен для расшифровки)
        return iv + encrypted_data

    def decrypt(self, encrypted_data: bytes) -> bytes:
        """Дешифрование данных"""
        try:
            # Извлекаем IV из начала пакета
            iv = encrypted_data[:self.block_size]
            actual_data = encrypted_data[self.block_size:]

            # Инициализируем шифр с тем же ключом и извлеченным IV
            cipher = gostcipher.new('kuznechik', self.key, gostcipher.MODE_CBC, init_vect=iv)

            # Расшифровываем
            decrypted_padded = cipher.decrypt(actual_data)

            # Удаляем паддинг
            return unpad(decrypted_padded, self.block_size)
        except ValueError as e:
            # Ошибка паддинга обычно означает неверный ключ
            print(f"Decryption error (Padding): {e}")
            raise e
        except Exception as e:
            print(f"Critical Decryption error: {e}")
            raise e

    def encrypt_b64(self, data: bytes) -> str:
        """Шифрование с кодированием в base64 (для конфигов/текста)"""
        encrypted = self.encrypt(data)
        return base64.b64encode(encrypted).decode('utf-8')

    def decrypt_b64(self, encrypted_b64: str) -> bytes:
        """Дешифрование из base64"""
        encrypted_data = base64.b64decode(encrypted_b64)
        return self.decrypt(encrypted_data)