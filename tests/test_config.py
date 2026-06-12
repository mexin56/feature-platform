from backend.config import Settings


def test_settings_dirs(tmp_path):
    s = Settings(storage_dir=str(tmp_path))
    s.ensure_dirs()
    assert s.db_path == tmp_path / "meta.db"
    assert s.online_db_path == tmp_path / "online_store.db"
    assert (tmp_path / "offline").is_dir()
    assert (tmp_path / "logs").is_dir()
    assert (tmp_path / "scripts").is_dir()
    assert s.sync_scheduler is False
