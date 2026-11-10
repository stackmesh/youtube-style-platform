import re

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1)
_DUMMY_HASH = _HASHER.hash("not-a-registered-password")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_HANDLE = re.compile(r"^[a-z0-9_]{3,30}$")
_MIN_PASSWORD = 10
_MAX_PASSWORD = 128


def normalize_email(value: str) -> str:
    return value.strip().lower()


def email_is_valid(value: str) -> bool:
    normalized = normalize_email(value)
    return 3 <= len(normalized) <= 254 and _EMAIL.fullmatch(normalized) is not None


def normalize_handle(value: str) -> str:
    return value.strip().lower()


def handle_is_valid(value: str) -> bool:
    return _HANDLE.fullmatch(normalize_handle(value)) is not None


def password_is_valid(value: str) -> bool:
    return _MIN_PASSWORD <= len(value) <= _MAX_PASSWORD


def hash_password(password: str) -> str:
    return _HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _HASHER.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def verify_dummy_password(password: str) -> None:
    """Spend the same Argon2 work as a real miss so timing does not reveal the account."""
    verify_password(password, _DUMMY_HASH)
