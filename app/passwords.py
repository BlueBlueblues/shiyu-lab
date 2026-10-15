import hashlib
import secrets
from uuid import NAMESPACE_URL, uuid5

LEGACY_ADMIN_ID = str(uuid5(NAMESPACE_URL, "firstorder-lab/legacy-administrator"))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=32768, r=8, p=3,
                            maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt$32768$8$3${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$")
        if (algorithm, n, r, p) != ("scrypt", "32768", "8", "3"):
            return False
        digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt),
                                n=32768, r=8, p=3, maxmem=64 * 1024 * 1024, dklen=32)
        return secrets.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False
