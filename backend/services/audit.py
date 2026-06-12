"""操作留痕:关键动作写 audit_logs(调用方负责 commit)。"""
from ..models import AuditLog


def record(db, user, action: str, detail: str = "", project_id: int | None = None) -> None:
    db.add(AuditLog(project_id=project_id, user_id=user.id if user else None,
                    action=action, detail=detail))
