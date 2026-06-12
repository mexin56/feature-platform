"""平台密钥:storage/.secret_key,首次生成 Fernet key 并落盘。
同一密钥用于 JWT 签名与连接密码加密(Phase 1b)。"""
from pathlib import Path

from cryptography.fernet import Fernet


def secret_key(storage_dir: Path) -> bytes:
    storage_dir = Path(storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    f = storage_dir / ".secret_key"
    if not f.exists():
        f.write_bytes(Fernet.generate_key())
    return f.read_bytes()
