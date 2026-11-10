import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """UUID version 7: millisecond timestamp plus random bits."""
    timestamp = int(time.time() * 1000) & 0xFFFFFFFFFFFF
    random_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF
    random_b = int.from_bytes(os.urandom(8), "big") & 0x3FFFFFFFFFFFFFFF
    value = (timestamp << 80) | (0x7 << 76) | (random_a << 64) | (0b10 << 62) | random_b
    return uuid.UUID(int=value)
