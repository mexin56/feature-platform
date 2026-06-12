"""任务执行入口:既是 multiprocessing.Process 的 target(顶层可导入,Windows spawn 兼容),
也可被 sync 模式直接调用。职责:日志重定向 → 心跳线程 → 执行插件 → 写终态。
前置:执行器已完成原子抢占(state=running, try_number 已 +1)。"""
import contextlib
import json
import threading
import traceback
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

HEARTBEAT_INTERVAL_SEC = 15


def run_task(db_path: str, ti_id: int, storage_dir: str) -> None:
    from ..config import Settings
    from ..db import make_engine
    from ..models import TaskInstance, WorkflowRun
    from ..services.plugins import get_plugin
    from ..services.templating import build_context

    settings = Settings(storage_dir=storage_dir)
    settings.ensure_dirs()
    engine = make_engine(db_path)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        ti = db.get(TaskInstance, ti_id)
        run = db.get(WorkflowRun, ti.run_id)
        log_dir = settings.logs_dir / f"run_{run.id}"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{ti.task_key}_try{ti.try_number}.log"
        ti.log_path = str(log_path)
        db.commit()
        params = json.loads(ti.params_json)
        ctx = build_context(run.data_interval_start, run.data_interval_end)
        task_type, task_key, try_number, max_tries = (
            ti.task_type, ti.task_key, ti.try_number, ti.max_tries)

    stop = threading.Event()

    def _beat():
        while not stop.wait(HEARTBEAT_INTERVAL_SEC):
            with Session() as hb:
                row = hb.get(TaskInstance, ti_id)
                if row is None or row.state != "running":
                    return
                row.heartbeat_at = datetime.utcnow()
                hb.commit()

    beater = threading.Thread(target=_beat, daemon=True)
    beater.start()

    state, result_json = "failed", None
    with open(log_path, "a", encoding="utf-8") as f, \
            contextlib.redirect_stdout(f), contextlib.redirect_stderr(f):
        print(f"[task_runner] {task_key} try {try_number}/{max_tries} type={task_type}")
        try:
            fn = get_plugin(task_type)
            result = fn(params, ctx, settings)
            result_json = json.dumps(result, ensure_ascii=False)
            state = "success"
            print(f"[task_runner] success: {result_json}")
        except Exception:
            traceback.print_exc()
            state = "up_for_retry" if try_number < max_tries else "failed"
            print(f"[task_runner] -> {state}")
    stop.set()

    with Session() as db:
        ti = db.get(TaskInstance, ti_id)
        if ti.state == "running":  # 可能已被 stop/孤儿清理改写,不覆盖
            ti.state = state
            ti.result_json = result_json
            ti.finished_at = datetime.utcnow()
            db.commit()
    engine.dispose()
