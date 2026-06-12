from backend.services.auth import (
    create_token, decode_token, hash_password, verify_password,
)
from backend.services.secrets import secret_key


def test_password_hash_roundtrip():
    h = hash_password("s3cret!")
    assert h != "s3cret!"
    assert verify_password("s3cret!", h) is True
    assert verify_password("wrong", h) is False
    assert verify_password("anything", "not-a-hash") is False


def test_secret_key_persistent(tmp_path):
    k1 = secret_key(tmp_path)
    k2 = secret_key(tmp_path)
    assert k1 == k2  # 同目录重复获取一致(落盘持久化)
    assert (tmp_path / ".secret_key").exists()


def test_token_roundtrip(tmp_path):
    token = create_token(42, tmp_path)
    assert decode_token(token, tmp_path) == 42
    assert decode_token("garbage", tmp_path) is None
