"""ULID generator. Monotonic within process. 26-char Crockford base32."""
from __future__ import annotations
import os, time, threading
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_lock = threading.Lock()
_last_ms = 0
_last_rand = 0

def new_ulid() -> str:
    """Generate a Crockford-base32 ULID (26 chars). Monotonic within process."""
    global _last_ms, _last_rand
    with _lock:
        ms = int(time.time() * 1000)
        if ms <= _last_ms:
            ms = _last_ms
            _last_rand += 1
            rand = _last_rand
        else:
            _last_ms = ms
            rand = int.from_bytes(os.urandom(10), "big")
            _last_rand = rand
        time_part = _encode(ms, 10, 6)   # 48 bits → 10 chars
        rand_part = _encode(rand, 16, 10)  # 80 bits → 16 chars
        return time_part + rand_part

def _encode(value: int, length: int, word_bits: int) -> str:
    out = []
    for _ in range(length):
        out.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))
