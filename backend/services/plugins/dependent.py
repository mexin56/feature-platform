"""dependent 插件:检查目标工作流在相同 data_interval 是否已成功。
未满足 → 抛错,由任务重试机制实现轮询等待(retries=轮询次数,retry_delay_sec=间隔)。
params: {workflow_id}"""
from datetime import datetime


def execute(params: dict, ctx: dict, env) -> dict:
    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker

    from ...db import make_engine
    from ...models import WorkflowRun

    target = int(params["workflow_id"])
    interval_start = datetime.strptime(ctx["data_interval_start"], "%Y-%m-%d %H:%M:%S")
    engine = make_engine(env.db_path)
    try:
        Session = sessionmaker(bind=engine)
        with Session() as db:
            ok = db.scalar(select(WorkflowRun.id).where(
                WorkflowRun.workflow_id == target,
                WorkflowRun.data_interval_start == interval_start,
                WorkflowRun.state == "success").limit(1))
    finally:
        engine.dispose()
    if not ok:
        raise RuntimeError(f"依赖未满足:工作流 {target} 在区间起点 "
                           f"{ctx['data_interval_start']} 尚未成功")
    return {"satisfied": True}
