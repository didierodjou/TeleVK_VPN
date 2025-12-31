
import gzip



class Compressor:
    """Компрессор данных"""

    @staticmethod
    def compress(data: bytes) -> bytes:
        """Сжатие данных"""
        return gzip.compress(data)

    @staticmethod
    def decompress(compressed_data: bytes) -> bytes:
        """Распаковка данных"""
        return gzip.decompress(compressed_data)