# -*- coding: utf-8 -*-
"""全量数据集采集探活:遍历 CATALOG,逐个调 fetch(不写库,避免并发写冲突),
记录成败/行数/列数/错误摘要。逐股类给 2 个样本股,每个数据集硬超时 70s。"""
import sys
import time
import threading

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

from backend.services.collectors import CATALOG, available  # noqa: E402

CTX = {"data_interval_end": "2026-06-12 17:00:00", "ds": "2026-06-12",
       "ds_nodash": "20260612", "data_interval_start": "2026-06-12 00:00:00"}
SYMBOLS = ["000001", "600519"]
TIMEOUT = 70


def run_one(ds):
    args = {"symbols": SYMBOLS, "interval_sec": 0} if ds.mode == "per_symbol" else {}
    box = {}

    def work():
        try:
            cols, rows = ds.fetch(args, CTX)
            box["ok"] = (len(cols), len(rows))
        except Exception as e:  # noqa: BLE001
            box["err"] = f"{type(e).__name__}: {str(e)[:160]}"

    th = threading.Thread(target=work, daemon=True)
    t0 = time.monotonic()
    th.start()
    th.join(TIMEOUT)
    dur = time.monotonic() - t0
    if th.is_alive():
        return ("TIMEOUT", f">{TIMEOUT}s", dur)
    if "ok" in box:
        ncol, nrow = box["ok"]
        return ("OK" if nrow > 0 else "EMPTY", f"{nrow}行/{ncol}列", dur)
    return ("FAIL", box.get("err", "?"), dur)


def main():
    keys = sorted(CATALOG.keys())
    by_src = {}
    for k in keys:
        by_src.setdefault(k.split(".")[0], []).append(k)

    results = []
    for src in sorted(by_src):
        for k in by_src[src]:
            ds = CATALOG[k]
            ok, reason = available(ds)
            if not ok:
                results.append((k, ds.mode, "SKIP", reason, 0.0))
                print(f"  SKIP  {k:42s} {reason}", flush=True)
                continue
            status, detail, dur = run_one(ds)
            results.append((k, ds.mode, status, detail, dur))
            print(f"  {status:7s} {k:42s} {detail}  ({dur:.1f}s)", flush=True)

    print("\n==== 汇总 ====")
    agg = {}
    for _, _, status, _, _ in results:
        agg[status] = agg.get(status, 0) + 1
    print("  " + "  ".join(f"{s}={n}" for s, n in sorted(agg.items())))
    print("\n==== 需关注(非 OK/SKIP/EMPTY) ====")
    for k, mode, status, detail, _ in results:
        if status in ("FAIL", "TIMEOUT"):
            print(f"  [{status}] {k} ({mode}): {detail}")
    print("\n==== EMPTY(通但 0 行,多为非交易日/盘中/无该股数据) ====")
    for k, mode, status, detail, _ in results:
        if status == "EMPTY":
            print(f"  [EMPTY] {k} ({mode})")


if __name__ == "__main__":
    main()
