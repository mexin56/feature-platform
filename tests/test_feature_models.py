import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.db import Base, make_engine
from backend.models import Feature, FeatureGroup, LineageEdge


def _session(tmp_path):
    engine = make_engine(tmp_path / "meta.db")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_feature_group_defaults_and_children(tmp_path):
    Session = _session(tmp_path)
    with Session() as db:
        fg = FeatureGroup(project_id=None, name="cust_daily", entity_keys_json='["cust_no"]',
                          offline_kind="parquet", offline_location="cust_daily")
        db.add(fg)
        db.flush()
        db.add(Feature(feature_group_id=fg.id, name="amt_30d", dtype="double",
                       description="近30天交易额"))
        db.add(LineageEdge(project_id=None, src="table:dw.cust_base",
                           dst=f"feature_group:{fg.id}"))
        db.commit()
        got = db.query(FeatureGroup).one()
        assert got.version == 1
        assert got.online_enabled is False
        assert got.last_produced_at is None
        assert got.materialize_watermark is None
        assert db.query(Feature).one().description == "近30天交易额"
        assert db.query(LineageEdge).one().src == "table:dw.cust_base"


def test_feature_group_unique_per_project_name_version(tmp_path):
    Session = _session(tmp_path)
    with Session() as db:
        from backend.models import User, Project
        u = User(username="owner", password_hash="x", role="developer")
        db.add(u)
        db.flush()
        p = Project(name="p1", owner_id=u.id)
        db.add(p)
        db.flush()

        db.add(FeatureGroup(project_id=p.id, name="g", version=1, entity_keys_json='["k"]',
                            offline_kind="parquet", offline_location="g"))
        db.commit()
        db.add(FeatureGroup(project_id=p.id, name="g", version=1, entity_keys_json='["k"]',
                            offline_kind="parquet", offline_location="g"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_feature_unique_per_group(tmp_path):
    Session = _session(tmp_path)
    with Session() as db:
        fg = FeatureGroup(project_id=None, name="g", entity_keys_json='["k"]',
                          offline_kind="parquet", offline_location="g")
        db.add(fg)
        db.flush()
        db.add(Feature(feature_group_id=fg.id, name="f1", dtype="int"))
        db.commit()
        db.add(Feature(feature_group_id=fg.id, name="f1", dtype="int"))
        with pytest.raises(IntegrityError):
            db.commit()
