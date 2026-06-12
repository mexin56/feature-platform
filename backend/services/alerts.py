"""告警产生与分发:落 alerts 表 + Webhook(全局配置)。调用方负责 commit 落库部分。"""
from ..models import Alert


def _send(db, title: str, text: str) -> None:
    """读取全局 webhook 配置并发送(失败已在 notify 内吞掉)。"""
    from .notify import get_setting, send_webhook

    send_webhook(get_setting(db, "webhook_url"), title, text)


def emit(db, *, project_id, level, kind, title, detail="",
         workflow_id=None, run_id=None, webhook=True) -> None:
    db.add(Alert(project_id=project_id, level=level, kind=kind, title=title,
                 detail=detail, workflow_id=workflow_id, run_id=run_id))
    if webhook:
        _send(db, title, detail or title)


def on_run_finished(db, wf, run) -> None:
    """run 完结钩子(advance_runs 完结判定后调用,随外层 commit 一起落库)。"""
    if run.state == "failed" and wf.alert_on_failure:
        emit(db, project_id=wf.project_id, level="error", kind="run_failed",
             title=f"工作流「{wf.name}」运行失败",
             detail=f"run_id={run.id} 区间 {run.data_interval_start:%Y-%m-%d %H:%M} ~ "
                    f"{run.data_interval_end:%Y-%m-%d %H:%M}",
             workflow_id=wf.id, run_id=run.id)
    elif run.state == "success" and wf.alert_on_success:
        emit(db, project_id=wf.project_id, level="info", kind="run_success",
             title=f"工作流「{wf.name}」运行成功",
             detail=f"run_id={run.id}", workflow_id=wf.id, run_id=run.id)
