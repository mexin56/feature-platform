import pytest
from sqlalchemy.orm import sessionmaker

from backend.db import Base, make_engine
from backend.models import AuditLog, Project, ProjectMember, User


def test_create_all_and_insert(tmp_path):
    engine = make_engine(tmp_path / "meta.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        u = User(username="alice", password_hash="x", role="developer")
        db.add(u)
        db.flush()
        p = Project(name="反欺诈特征", description="", owner_id=u.id)
        db.add(p)
        db.flush()
        db.add(ProjectMember(project_id=p.id, user_id=u.id))
        db.add(AuditLog(project_id=p.id, user_id=u.id, action="create_project", detail="反欺诈特征"))
        db.commit()
        assert db.query(User).one().is_active is True
        assert db.query(Project).one().owner_id == u.id
        assert db.query(AuditLog).one().action == "create_project"


def test_wal_mode(tmp_path):
    engine = make_engine(tmp_path / "meta.db")
    with engine.connect() as conn:
        mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
    assert mode == "wal"


def test_project_member_unique(tmp_path):
    from sqlalchemy.exc import IntegrityError

    engine = make_engine(tmp_path / "meta.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        u = User(username="alice", password_hash="x", role="developer")
        db.add(u)
        db.flush()
        p = Project(name="p1", description="", owner_id=u.id)
        db.add(p)
        db.flush()
        db.add(ProjectMember(project_id=p.id, user_id=u.id))
        db.commit()
        db.add(ProjectMember(project_id=p.id, user_id=u.id))
        with pytest.raises(IntegrityError):
            db.commit()
