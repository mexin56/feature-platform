"""认证:bcrypt 密码哈希 + JWT(密钥复用 storage/.secret_key)。"""
from datetime import datetime, timedelta
from pathlib import Path

ALGO = "HS256"
TOKEN_HOURS = 12


def hash_password(plaintext: str) -> str:
    import bcrypt

    return bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt()).decode()


def verify_password(plaintext: str, hashed: str) -> bool:
    import bcrypt

    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), hashed.encode())
    except ValueError:
        return False


def create_token(user_id: int, storage_dir: Path) -> str:
    from jose import jwt

    from .secrets import secret_key

    payload = {
        "sub": str(user_id),
        "exp": datetime.utcnow() + timedelta(hours=TOKEN_HOURS),
    }
    return jwt.encode(payload, secret_key(storage_dir).decode(), algorithm=ALGO)


def decode_token(token: str, storage_dir: Path) -> int | None:
    from jose import JWTError, jwt

    from .secrets import secret_key

    try:
        return int(
            jwt.decode(token, secret_key(storage_dir).decode(), algorithms=[ALGO])["sub"]
        )
    except (JWTError, KeyError, ValueError):
        return None
