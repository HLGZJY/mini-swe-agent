"""聚合全部批次 → 消融矩阵（markdown + json）。

批次映射（同一任务的"最终口径"数据源）：
  p1/p3 → pilot2，p2 → pilot3（二次回炉版），r1-r6 → scale1，d1/d2/l1/l2 → scale2

信号口径（SQL，逐格）：
  errs = extra.error_class 非空事件数；stall = extra.stall_event 数；
  comp = sessions.compression_count；maxPT = usage.prompt_tokens 峰值
用法：python aggregate.py [--out results/summary]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

BATCH_FOR_TASK = {
    "p1_fingerprint_regression": "pilot2",
    "p3_long_crossref": "pilot2",
    "p2_deadend_retrykit": "pilot3",
    "r1_pagination_boundary": "scale1",
    "r2_config_deep_merge": "scale1",
    "r3_ts_tz_rule": "scale1",
    "r4_phone_normalize": "scale1",
    "r5_lru_recency": "scale1",
    "r6_token_bucket": "scale1",
    "d1_offline_fxapi": "scale2",
    "d2_binary_sdk_only": "scale2",
    "l1_long_slowtrace": "scale2",
    "l2_long_teamrollup": "scale2",
}
VERSIONS = ["v0_bash_only", "v1_tc_noretry", "v2_retry", "v3_compress", "v4_stall"]
V_SHORT = {"v0_bash_only": "V0", "v1_tc_noretry": "V1", "v2_retry": "V2", "v3_compress": "V3", "v4_stall": "V4"}


def cell_signals(cell_dir: Path) -> dict:
    db = cell_dir / "session.db"
    if not db.exists():
        return {"errs": 0, "stall": 0, "comp": 0, "maxPT": 0}
    errs = stall = max_pt = 0
    con = sqlite3.connect(db)
    try:
        for (d,) in con.execute("select data from messages"):
            dd = json.loads(d) if d else {}
            ex = dd.get("extra") or {}
            if ex.get("error_class"):
                errs += 1
            if ex.get("stall_event"):
                stall += 1
            pt = ((ex.get("response") or {}).get("usage") or {}).get("prompt_tokens") or 0
            if pt > max_pt:
                max_pt = pt
        comp = con.execute("select compression_count from sessions").fetchone()[0]
    finally:
        con.close()
    return {"errs": errs, "stall": stall, "comp": comp, "maxPT": max_pt}


def load_cell(batch: str, task: str, version: str) -> dict | None:
    preds_path = RESULTS / batch / "preds.json"
    if not preds_path.exists():
        return None
    preds = json.loads(preds_path.read_text(encoding="utf-8"))
    cell = preds.get(f"{task}::{version}")
    if not cell or cell.get("status") != "done":
        return None
    out = {
        "judge": cell.get("judge"),
        "steps": cell.get("api_calls"),
        "cost": round(cell.get("instance_cost", 0.0), 3),
    }
    out.update(cell_signals(RESULTS / batch / f"{task}__{version}"))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="summary")
    args = ap.parse_args()

    sys.path.insert(0, str(HERE))

    matrix: dict[str, dict[str, dict]] = {}
    total_cost = 0.0
    for task, batch in BATCH_FOR_TASK.items():
        matrix[task] = {}
        for v in VERSIONS:
            cell = load_cell(batch, task, v)
            matrix[task][v] = cell or {}
            if cell:
                total_cost += cell["cost"]

    # ---- markdown ----
    n_cells = sum(1 for t in matrix.values() for v in t.values() if v)
    lines = [
        "# 魔改6 消融矩阵（13 题 × 5 版本）",
        "",
        f"总成本（最终口径 {len(BATCH_FOR_TASK)} 题 × 5 版 = {n_cells} 格）：${total_cost:.3f}",
        "",
        "| 任务 | 桶 | 版本 | 判定 | 步数 | 成本$ | errs | stall | comp | maxPT |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    def bucket_of(t: str) -> str:
        if t.startswith(("r1", "r2", "r3", "r4", "r5", "r6")) or t.startswith("p1"):
            return "常规"
        if t.startswith("d") or t == "p2_deadend_retrykit":
            return "死路"
        return "超长"

    for task, cells in matrix.items():
        for v in VERSIONS:
            c = cells.get(v) or {}
            lines.append(
                f"| {task} | {bucket_of(task)} | {V_SHORT[v]} | {c.get('judge', '-')} | "
                f"{c.get('steps', '-')} | {c.get('cost', '-')} | {c.get('errs', '-')} | "
                f"{c.get('stall', '-')} | {c.get('comp', '-')} | {c.get('maxPT', '-')} |"
            )

    # ---- 版本级汇总（简历口径：解决率/均步/均本）----
    lines += ["", "## 版本级汇总", "", "| 版本 | 解决率 | honest | 均步 | 均成本$ |", "|---|---|---|---|---|"]
    for v in VERSIONS:
        done = [matrix[t][v] for t in matrix if matrix[t].get(v)]
        solved = sum(1 for c in done if c.get("judge") == "solved")
        honest = sum(1 for c in done if c.get("judge") == "honest")
        steps = [c["steps"] for c in done if c.get("steps")]
        costs = [c["cost"] for c in done if c.get("cost")]
        ms = f"{sum(steps) / len(steps):.1f}" if steps else "-"
        mc = f"{sum(costs) / len(costs):.3f}" if costs else "-"
        lines.append(f"| {V_SHORT[v]} | {solved}/{len(done)} | {honest} | {ms} | {mc} |")

    out_md = RESULTS / f"{args.out}.md"
    out_json = RESULTS / f"{args.out}.json"
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out_json.write_text(
        json.dumps({"total_cost": round(total_cost, 3), "matrix": matrix}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print("\n".join(lines))
    print(f"\nwrote {out_md} / {out_json}")


if __name__ == "__main__":
    main()
