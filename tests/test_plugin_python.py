from datetime import datetime

import pytest

from backend.config import Settings
from backend.services.plugins import get_plugin
from backend.services.templating import build_context

CTX = build_context(datetime(2026, 6, 11), datetime(2026, 6, 12))


def _env(tmp_path):
    s = Settings(storage_dir=str(tmp_path))
    s.ensure_dirs()
    return s


def test_script_runs_with_env_vars(tmp_path, capsys):
    env = _env(tmp_path)
    (env.scripts_dir / "ok.py").write_text(
        "import os\nprint('ds=' + os.environ['FP_DS'])\n", encoding="utf-8")
    fn = get_plugin("python_script")
    result = fn({"script": "ok.py"}, CTX, env)
    assert result["returncode"] == 0
    assert "ds=2026-06-11" in capsys.readouterr().out  # 子进程 stdout 转写到当前 stdout(任务日志)


def test_script_nonzero_exit_raises(tmp_path):
    env = _env(tmp_path)
    (env.scripts_dir / "bad.py").write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
    fn = get_plugin("python_script")
    with pytest.raises(RuntimeError, match="退出码 3"):
        fn({"script": "bad.py"}, CTX, env)


def test_script_missing_raises(tmp_path):
    fn = get_plugin("python_script")
    with pytest.raises(FileNotFoundError):
        fn({"script": "ghost.py"}, CTX, _env(tmp_path))


def test_script_path_escape_rejected(tmp_path):
    fn = get_plugin("python_script")
    with pytest.raises(ValueError, match="脚本路径"):
        fn({"script": "../outside.py"}, CTX, _env(tmp_path))
