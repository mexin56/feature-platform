from backend.services.secrets import decrypt_text, encrypt_text


def test_encrypt_roundtrip(tmp_path):
    token = encrypt_text("p@ssw0rd", tmp_path)
    assert token != "p@ssw0rd"
    assert decrypt_text(token, tmp_path) == "p@ssw0rd"


def test_encrypt_differs_by_key(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    token = encrypt_text("x", a)
    import pytest
    from cryptography.fernet import InvalidToken

    with pytest.raises(InvalidToken):
        decrypt_text(token, b)
