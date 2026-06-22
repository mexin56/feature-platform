"""因子研究 API 路由:因子库CRUD / 计算 / 分析 / 策略回测。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..deps import get_current_user, get_db, get_project_id, get_settings
from ..models import (BacktestResult, Factor, FactorComputation, Strategy,
                      User)

router = APIRouter(prefix="/api", tags=["factor-research"])


# ═══════════════════════════════════════════════════════════
# 请求/响应模型
# ═══════════════════════════════════════════════════════════

class FactorCreate(BaseModel):
    name: str
    name_cn: str
    category: str = "custom"
    subcategory: str = ""
    description: str = ""
    formula_sql: str
    cross_sectional: bool = True
    direction: int = 1
    required_tables: str = "ods_tushare_daily"


class FactorUpdate(BaseModel):
    name_cn: str | None = None
    category: str | None = None
    subcategory: str | None = None
    description: str | None = None
    formula_sql: str | None = None
    cross_sectional: bool | None = None
    direction: int | None = None
    required_tables: str | None = None


class ComputeRequest(BaseModel):
    factor_ids: list[int]
    start_date: str
    end_date: str
    universe: str = "hs300"
    normalize: str = ""  # '' | 'zscore' | 'percentile'


class StrategyCreate(BaseModel):
    name: str
    description: str = ""
    factor_weights: dict[str, float]  # {factor_name: weight}
    top_n: int = 30
    rebalance_freq: str = "monthly"  # monthly/weekly/daily
    weight_scheme: str = "equal"  # equal/mcap/score
    benchmark: str = "hs300"
    start_date: str
    end_date: str
    transaction_cost_bps: int = 30


class StrategyUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    factor_weights: dict[str, float] | None = None
    top_n: int | None = None
    rebalance_freq: str | None = None
    weight_scheme: str | None = None
    transaction_cost_bps: int | None = None


# ═══════════════════════════════════════════════════════════
# 因子库 CRUD
# ═══════════════════════════════════════════════════════════

def _factor_out(f: Factor) -> dict:
    return {
        "id": f.id, "name": f.name, "name_cn": f.name_cn,
        "category": f.category, "subcategory": f.subcategory,
        "description": f.description,
        "formula_sql": f.formula_sql,
        "cross_sectional": f.cross_sectional,
        "direction": f.direction,
        "required_tables": f.required_tables,
        "is_builtin": f.is_builtin,
        "author": f.author,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


@router.get("/factors")
def list_factors(
    category: str | None = None,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """列出所有因子,可按 category 筛选。"""
    q = select(Factor).order_by(Factor.category, Factor.subcategory, Factor.name)
    if category and category != "__all__":
        q = q.where(Factor.category == category)
    rows = db.scalars(q).unique().all()
    return [_factor_out(r) for r in rows]


@router.get("/factors/categories")
def list_factor_categories(db=Depends(get_db), user=Depends(get_current_user)):
    """返回各分类及因子数。"""
    from sqlalchemy import func
    rows = db.execute(
        select(Factor.category, func.count(Factor.id))
        .group_by(Factor.category).order_by(Factor.category)
    ).all()
    return [{"category": r[0], "count": r[1]} for r in rows]


@router.get("/factors/{fid}")
def get_factor(fid: int, db=Depends(get_db), user=Depends(get_current_user)):
    f = db.get(Factor, fid)
    if not f:
        raise HTTPException(404, "因子不存在")
    return _factor_out(f)


@router.post("/factors")
def create_factor(
    body: FactorCreate,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """新增自定义因子。名称重复返回 409。"""
    existing = db.scalar(select(Factor).where(Factor.name == body.name))
    if existing:
        raise HTTPException(409, f"因子名 '{body.name}' 已存在")
    f = Factor(**body.model_dump(), is_builtin=False)
    db.add(f)
    db.commit()
    db.refresh(f)
    return _factor_out(f)


@router.put("/factors/{fid}")
def update_factor(
    fid: int,
    body: FactorUpdate,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """编辑因子(仅自定义因子)。"""
    f = db.get(Factor, fid)
    if not f:
        raise HTTPException(404, "因子不存在")
    if f.is_builtin:
        raise HTTPException(403, "内置因子不可修改,请新建自定义因子")
    updates = body.model_dump(exclude_none=True)
    for k, v in updates.items():
        setattr(f, k, v)
    db.commit()
    db.refresh(f)
    return _factor_out(f)


@router.delete("/factors/{fid}")
def delete_factor(fid: int, db=Depends(get_db), user=Depends(get_current_user)):
    f = db.get(Factor, fid)
    if not f:
        raise HTTPException(404, "因子不存在")
    if f.is_builtin:
        raise HTTPException(403, "内置因子不可删除")
    db.delete(f)
    db.commit()
    return {"ok": True}


# ═══════════════════════════════════════════════════════════
# 成分股
# ═══════════════════════════════════════════════════════════

@router.get("/universe/hs300")
def get_hs300_universe(
    date: str | None = None,
    settings=Depends(get_settings),
    db=Depends(get_db),
):
    """获取沪深 300 成分股列表(date 不传则用今天)。"""
    from ..services.universe import get_constituents

    dt = date or __import__("datetime").datetime.now().strftime("%Y-%m-%d")
    codes = get_constituents("hs300", dt, settings, db)
    return {"codes": codes, "count": len(codes), "as_of": dt}


@router.post("/universe/hs300/refresh")
def refresh_hs300(
    trade_date: str | None = None,
    settings=Depends(get_settings),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """从 tushare 刷新 HS300 成分股。"""
    from ..services.universe import refresh

    dt = trade_date or __import__("datetime").datetime.now().strftime("%Y-%m-%d")
    n = refresh("hs300", dt, settings, db)
    return {"ok": True, "rows": n}


# ═══════════════════════════════════════════════════════════
# 因子计算
# ═══════════════════════════════════════════════════════════

@router.post("/factors/compute")
def run_factor_compute(
    body: ComputeRequest,
    settings=Depends(get_settings),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """执行因子批量计算,产出到 factors.db。"""
    from ..services.factor_engine import compute_factors
    from ..services.universe import get_constituents

    # 取因子定义
    factors = [db.get(Factor, fid) for fid in body.factor_ids]
    missing = [fid for fid, f in zip(body.factor_ids, factors) if f is None]
    if missing:
        raise HTTPException(404, f"因子不存在: {missing}")

    # 取成分股
    try:
        codes = get_constituents(body.universe, body.end_date, settings, db)
    except RuntimeError as e:
        raise HTTPException(400, str(e))

    if not codes:
        raise HTTPException(400, f"{body.universe} 成分股为空,请先刷新")

    specs = [{k: getattr(f, k) for k in (
        "name", "formula_sql", "required_tables", "direction", "cross_sectional")}
             for f in factors]

    # 记录 computation
    comp = FactorComputation(
        factor_ids=str(body.factor_ids),
        start_date=body.start_date,
        end_date=body.end_date,
        universe=body.universe,
        status="running",
        created_by=user.id,
    )
    db.add(comp)
    db.commit()
    db.refresh(comp)

    try:
        result = compute_factors(
            specs, body.start_date, body.end_date, codes, settings,
            normalize=body.normalize,
        )
        comp.status = "done"
        comp.rows = result["rows"]
        comp.output_path = result["output_path"]
        db.commit()
        return {
            "computation_id": comp.id,
            "rows": result["rows"],
            "factor_names": result["factor_names"],
        }
    except Exception as e:
        comp.status = "failed"
        comp.error_msg = str(e)
        db.commit()
        raise HTTPException(500, str(e))


@router.get("/factors/computations")
def list_computations(db=Depends(get_db), user=Depends(get_current_user)):
    rows = db.execute(
        select(FactorComputation).order_by(FactorComputation.created_at.desc()).limit(20)
    ).scalars().unique().all()
    return [
        {
            "id": r.id, "factor_ids": r.factor_ids, "start_date": r.start_date,
            "end_date": r.end_date, "universe": r.universe,
            "status": r.status, "rows": r.rows, "error_msg": r.error_msg,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/factors/values")
def get_factor_values(
    factor_names: str | None = None,  # 逗号分隔,不传=全部
    start_date: str | None = None,
    end_date: str | None = None,
    settings=Depends(get_settings),
    user=Depends(get_current_user),
):
    """查询 factors.db 中最近一次计算的因子值(限制 2000 行)。"""
    import duckdb
    from pathlib import Path

    fp = Path(settings.storage_dir / "factors.db")
    if not fp.exists():
        return {"columns": [], "rows": [], "row_count": 0}

    con = duckdb.connect(str(fp), read_only=True)
    try:
        cnt = con.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name='factor_values_latest'"
        ).fetchone()[0]
        if not cnt:
            return {"columns": [], "rows": [], "row_count": 0}

        cols = [r[0] for r in con.execute("DESCRIBE factor_values_latest").fetchall()]
        cols_str = ", ".join(f'"{c}"' for c in cols)
        where = []
        params = []
        if start_date:
            where.append("trade_date >= ?")
            params.append(start_date)
        if end_date:
            where.append("trade_date <= ?")
            params.append(end_date)
        wsql = ("WHERE " + " AND ".join(where)) if where else ""
        sql = f"SELECT {cols_str} FROM factor_values_latest {wsql} ORDER BY trade_date, ts_code LIMIT 2000"
        rows_df = con.execute(sql, params).fetchdf()
        return {
            "columns": cols,
            "rows": rows_df.where(rows_df.notna(), None).values.tolist(),
            "row_count": len(rows_df),
        }
    finally:
        con.close()


# ═══════════════════════════════════════════════════════════
# 因子分析
# ═══════════════════════════════════════════════════════════

@router.get("/factors/{fid}/analysis")
def single_factor_analysis(
    fid: int,
    start_date: str,
    end_date: str,
    forward_period: int = 1,
    settings=Depends(get_settings),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """单因子 IC + 分位数 + 衰减分析。"""
    from ..services.factor_analysis import analyze_factor

    f = db.get(Factor, fid)
    if not f:
        raise HTTPException(404, "因子不存在")
    try:
        return analyze_factor(
            f.name, start_date, end_date, forward_period, settings)
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@router.post("/factors/combine")
def factor_combine(
    factor_weights: dict[str, float],
    settings=Depends(get_settings),
    user=Depends(get_current_user),
):
    """多因子合成:加权组合 + IC 回测。"""
    from ..services.factor_analysis import combine_factors

    try:
        return combine_factors(factor_weights, settings=settings)
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@router.post("/factors/correlation-matrix")
def factor_correlation(
    factor_names: list[str],
    settings=Depends(get_settings),
    user=Depends(get_current_user),
):
    """因子间两两相关系数矩阵。"""
    from ..services.factor_analysis import correlation_matrix
    return correlation_matrix(factor_names, settings=settings)


@router.get("/factors/{fid}/decay")
def factor_decay(
    fid: int,
    start_date: str,
    end_date: str,
    settings=Depends(get_settings),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """单因子 IC 衰减(1/5/10/20/60 日)。"""
    import duckdb
    from pathlib import Path

    from ..services.factor_analysis import _compute_ic_decay

    f = db.get(Factor, fid)
    if not f:
        raise HTTPException(404, "因子不存在")
    fp = Path(settings.storage_dir / "factors.db")
    mp = Path(settings.market_db)
    con = duckdb.connect(str(fp))
    try:
        con.execute(f"ATTACH '{mp.as_posix()}' AS market (READ_ONLY)")
        return _compute_ic_decay(con, f.name, start_date, end_date)
    finally:
        con.close()


# ═══════════════════════════════════════════════════════════
# 策略与回测
# ═══════════════════════════════════════════════════════════

def _strategy_out(s: Strategy) -> dict:
    import json
    return {
        "id": s.id, "name": s.name, "description": s.description,
        "factor_weights": json.loads(s.factor_weights_json),
        "top_n": s.top_n, "rebalance_freq": s.rebalance_freq,
        "weight_scheme": s.weight_scheme, "benchmark": s.benchmark,
        "start_date": s.start_date, "end_date": s.end_date,
        "transaction_cost_bps": s.transaction_cost_bps,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


@router.get("/strategies")
def list_strategies(
    db=Depends(get_db),
    user=Depends(get_current_user),
    pid=Depends(get_project_id),
):
    rows = db.scalars(
        select(Strategy).where(Strategy.project_id == pid).order_by(Strategy.created_at.desc())
    ).unique().all()
    return [_strategy_out(r) for r in rows]


@router.post("/strategies")
def create_strategy(
    body: StrategyCreate,
    db=Depends(get_db),
    user=Depends(get_current_user),
    pid=Depends(get_project_id),
):
    import json
    s = Strategy(
        name=body.name, description=body.description,
        factor_weights_json=json.dumps(body.factor_weights, ensure_ascii=False),
        top_n=body.top_n, rebalance_freq=body.rebalance_freq,
        weight_scheme=body.weight_scheme, benchmark=body.benchmark,
        start_date=body.start_date, end_date=body.end_date,
        transaction_cost_bps=body.transaction_cost_bps,
        project_id=pid, created_by=user.id,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return _strategy_out(s)


@router.get("/strategies/{sid}")
def get_strategy(sid: int, db=Depends(get_db), user=Depends(get_current_user)):
    s = db.get(Strategy, sid)
    if not s:
        raise HTTPException(404, "策略不存在")
    return _strategy_out(s)


@router.put("/strategies/{sid}")
def update_strategy(
    sid: int,
    body: StrategyUpdate,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    s = db.get(Strategy, sid)
    if not s:
        raise HTTPException(404, "策略不存在")
    import json
    updates = body.model_dump(exclude_none=True)
    if "factor_weights" in updates:
        updates["factor_weights_json"] = json.dumps(updates.pop("factor_weights"), ensure_ascii=False)
    for k, v in updates.items():
        setattr(s, k, v)
    db.commit()
    db.refresh(s)
    return _strategy_out(s)


@router.delete("/strategies/{sid}")
def delete_strategy(sid: int, db=Depends(get_db), user=Depends(get_current_user)):
    s = db.get(Strategy, sid)
    if not s:
        raise HTTPException(404, "策略不存在")
    db.delete(s)
    db.commit()
    return {"ok": True}


@router.post("/strategies/{sid}/backtest")
def run_backtest(
    sid: int,
    settings=Depends(get_settings),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """运行策略回测,返回绩效指标。"""
    import json

    from ..services.backtest_engine import run_backtest as do_backtest

    s = db.get(Strategy, sid)
    if not s:
        raise HTTPException(404, "策略不存在")

    weights = json.loads(s.factor_weights_json) if s.factor_weights_json else {}
    try:
        result = do_backtest(
            factor_weights=weights,
            top_n=s.top_n,
            start_date=s.start_date,
            end_date=s.end_date,
            rebalance_freq=s.rebalance_freq,
            weight_scheme=s.weight_scheme,
            transaction_cost_bps=s.transaction_cost_bps,
            settings=settings,
        )
    except RuntimeError as e:
        raise HTTPException(400, str(e))

    # save result
    br = BacktestResult(
        strategy_id=sid,
        metrics_json=json.dumps(result["metrics"], ensure_ascii=False),
        returns_path=result.get("returns_path"),
        trades_path=result.get("trades_path"),
        status="done",
    )
    db.add(br)
    db.commit()
    db.refresh(br)

    return {
        "backtest_id": br.id,
        "metrics": result["metrics"],
        "daily_returns": result["daily_returns"],
    }


@router.get("/backtests/{bid}")
def get_backtest(bid: int, db=Depends(get_db), user=Depends(get_current_user)):
    import json
    br = db.get(BacktestResult, bid)
    if not br:
        raise HTTPException(404, "回测结果不存在")
    return {
        "id": br.id,
        "strategy_id": br.strategy_id,
        "metrics": json.loads(br.metrics_json),
        "status": br.status,
        "created_at": br.created_at.isoformat() if br.created_at else None,
    }


@router.get("/backtests/{bid}/returns")
def get_backtest_returns(
    bid: int,
    settings=Depends(get_settings),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """读取回测日收益序列(从 parquet 文件)。"""
    import duckdb
    from pathlib import Path

    br = db.get(BacktestResult, bid)
    if not br or not br.returns_path:
        return {"returns": []}
    fp = Path(br.returns_path)
    if not fp.exists():
        return {"returns": []}
    con = duckdb.connect()
    try:
        df = con.execute(f"SELECT * FROM '{fp.as_posix()}' ORDER BY trade_date").fetchdf()
        return {
            "returns": df.where(df.notna(), None).to_dict(orient="records"),
        }
    finally:
        con.close()
