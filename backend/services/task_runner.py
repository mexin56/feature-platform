"""任务执行入口:既是 multiprocessing.Process 的 target(顶层可导入,Windows spawn 兼容),
也可被 sync 模式直接调用。职责:日志重定向 → 心跳线程 → 执行插件 → 写终态。
前置:执行器已完成原子抢占(state=running, try_number 已 +1)。"""
import contextlib
import json
import threading
import traceback
from datetime import datetime

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
        run_workflow_id = run.workflow_id

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
        from sqlalchemy import update

        # 原子终态写入:仅当仍为 running 时生效(stop/孤儿清理改写过则不覆盖)
        db.execute(update(TaskInstance)
                   .where(TaskInstance.id == ti_id, TaskInstance.state == "running")
                   .values(state=state, result_json=result_json,
                           finished_at=datetime.utcnow()))
        db.commit()
        if state == "success":
            try:
                _register_production(Session, run_workflow_id, task_key, result_json)
            except Exception:  # noqa: BLE001  注册是元数据回写,失败不影响任务终态
                traceback.print_exc()
    engine.dispose()


def _register_production(Session, workflow_id: int, task_key: str,
                         result_json: str | None) -> None:
    """生产即注册:绑定 (workflow_id, task_key) 的特征组回写最近产出时间/行数。"""
    from sqlalchemy import select

    from ..models import FeatureGroup

    rows = None
    if result_json:
        try:
            rows = json.loads(result_json).get("rows")
        except (ValueError, AttributeError):
            rows = None
    with Session() as db:
        fgs = db.scalars(select(FeatureGroup).where(
            FeatureGroup.workflow_id == workflow_id,
            FeatureGroup.task_key == task_key)).all()
        for fg in fgs:
            fg.last_produced_at = datetime.utcnow()
            fg.last_produced_rows = rows
        if fgs:
            db.commit()
