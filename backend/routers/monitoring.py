from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select

from ..deps import get_db, get_project_id
from ..models import (
    Alert, FeatureGroup, TaskInstance, Workflow, WorkflowRun,
)
from ..services.alerts import emit
from ..services.notify import get_setting

router = APIRouter(tags=["monitoring"])


@router.get("/alerts")
def list_alerts(unread_only: int = 0, db=Depends(get_db), pid=Depends(get_project_id)):
    # NOTE: build where-clauses BEFORE order_by/limit — SQLAlchemy raises
    # InvalidRequestError if .where() is called after .limit() in some 2.x versions.
    # Semantics are identical to the plan; restructured for safety.
    q = select(Alert).where(Alert.project_id == pid)
    if unread_only:
        q = q.where(Alert.read.is_(False))
    q = q.order_by(Alert.id.desc()).limit(200)
    return [{"id": a.id, "level": a.level, "kind": a.kind, "title": a.title,
             "detail": a.detail, "workflow_id": a.workflow_id, "run_id": a.run_id,
             "read": a.read, "created_at": a.created_at.isoformat()}
            for a in db.scalars(q).all()]


@router.post("/alerts/{aid}/read")
def mark_read(aid: int, db=Depends(get_db), pid=Depends(get_project_id)):
    a = db.get(Alert, aid)
    if a is None or a.project_id != pid:
        raise HTTPException(404, "告警不存在")
    a.read = True
    db.commit()
    return {"ok": True}


@router.get("/monitoring/dashboard")
def dashboard(db=Depends(get_db), pid=Depends(get_project_id)):
    wf_ids = [w.id for w in db.scalars(
        select(Workflow).where(Workflow.project_id == pid)).all()]
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    def _count(state):
        if not wf_ids:
            return 0
        return db.scalar(select(func.count(WorkflowRun.id)).where(
            WorkflowRun.workflow_id.in_(wf_ids),
            WorkflowRun.created_at >= today,
            WorkflowRun.state == state)) or 0

    failures = []
    if wf_ids:
        rows = db.scalars(select(WorkflowRun).where(
            WorkflowRun.workflow_id.in_(wf_ids), WorkflowRun.state == "failed")
            .order_by(WorkflowRun.id.desc()).limit(10)).all()
        failures = [{"run_id": r.id, "workflow_id": r.workflow_id,
                     "interval": r.data_interval_start.isoformat(),
                     "finished_at": r.finished_at.isoformat() if r.finished_at else None}
                    for r in rows]

    fgs = db.scalars(select(FeatureGroup).where(FeatureGroup.project_id == pid)).all()
    now = datetime.utcnow()
    try:
        lag_threshold = float(get_setting(db, "materialize_lag_hours", "24"))
    except ValueError:
        lag_threshold = 24.0
    fg_out = []
    for fg in fgs:
        lag_hours = None
        if fg.online_enabled and fg.materialize_watermark is not None:
            lag_hours = round((now - fg.materialize_watermark).total_seconds() / 3600, 1)
            if lag_hours > lag_threshold:
                dup = db.scalar(select(Alert.id).where(
                    Alert.kind == "materialize_lag", Alert.project_id == pid,
                    Alert.detail.like(f"fg_id={fg.id};%"),
                    Alert.created_at >= today).limit(1))
                if dup is None:
                    emit(db, project_id=pid, level="warning", kind="materialize_lag",
                         title=f"特征组「{fg.name}」在线物化滞后",
                         detail=f"fg_id={fg.id};水位落后 {lag_hours} 小时(阈值 {lag_threshold})",
                         workflow_id=fg.workflow_id, webhook=False)
        fg_out.append({"id": fg.id, "name": fg.name, "version": fg.version,
                       "online_enabled": fg.online_enabled,
                       "last_produced_at": (fg.last_produced_at.isoformat()
                                            if fg.last_produced_at else None),
                       "lag_hours": lag_hours})
    db.commit()  # 落滞后告警
    return {"today": {"success": _count("success"), "failed": _count("failed"),
                      "running": _count("running")},
            "recent_failures": failures,
            "workflows_total": len(wf_ids),
            "feature_groups": fg_out}
