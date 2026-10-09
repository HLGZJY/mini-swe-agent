#!/usr/bin/env python3
"""魔改6 批跑器：串行执行 (task × version) 格子矩阵。

特性（仿 run/benchmarks/swebench.py 的 preds.json 断点语义）：
  - 进度簿 preds.json：键 `task::version`，含 status / cost / judge；重跑自动跳过已完成格
  - 看门狗：单格子进程硬超时（--timeout 秒，默认 780 = 600 agent 上限 + 缓冲）
  - 判定：每格跑完立即调任务 checker.py（0=solved / 2=honest / 1=fail）
  - 成本簿：每格打印累计成本；汇总写 summary.json

用法（仓库根，venv python）：
  .venv/Scripts/python.exe scripts/ablation/run.py --batch pilot
  .venv/Scripts/python.exe scripts/ablation/run.py --batch pilot --tasks p1 --versions v0_bash_only --rerun p1::v0_bash_only
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
VENV_PY = REPO / ".venv" / "Scripts" / "python.exe"

ALL_TASKS = [
    "p1_fingerprint_regression",
    "p2_deadend_retrykit",
    "p3_long_crossref",
    "r1_pagination_boundary",
    "r2_config_deep_merge",
    "r3_ts_tz_rule",
    "r4_phone_normalize",
    "r5_lru_recency",
    "r6_token_bucket",
    "d1_offline_fxapi",
    "d2_binary_sdk_only",
    "l1_long_slowtrace",
    "l2_long_teamrollup",
]


def load_preds(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_preds(path: Path, preds: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(preds, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def run_cell(task: str, version: str, cell_dir: Path, timeout: int) -> dict:
    cell_dir.mkdir(parents=True, exist_ok=True)
    log_path = cell_dir / "run.log"
    cmd = [
        str(VENV_PY),
        str(HERE / "_run_one.py"),
        "--task-dir",
        str(HERE / "tasks" / task),
        "--version",
        version,
        "--cell-dir",
        str(cell_dir),
    ]
    start = time.time()
    with log_path.open("w", encoding="utf-8") as log:
        try:
            proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, cwd=str(REPO))
            status = "done" if proc.returncode == 0 else f"exit{proc.returncode}"
        except subprocess.TimeoutExpired:
            log.write(f"\n[watchdog] killed after {timeout}s\n", encoding="utf-8")
            status = "hung"
    elapsed = time.time() - start

    result_path = cell_dir / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    result["status"] = status
    result["elapsed_s"] = round(elapsed, 1)
    return result


def judge(task: str, cell_dir: Path) -> dict:
    checker = HERE / "tasks" / task / "checker.py"
    workdir = cell_dir / "workdir"
    try:
        proc = subprocess.run(
            [str(VENV_PY), str(checker), "--workdir", str(workdir)],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return {"judge": "fail", "judge_note": "checker timeout"}
    out = (proc.stdout or "").strip().splitlines()
    last = out[-1] if out else ""
    if proc.returncode == 0:
        return {"judge": "solved", "judge_note": last}
    if proc.returncode == 2:
        return {"judge": "honest", "judge_note": last}
    return {"judge": "fail", "judge_note": last}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", required=True, help="批次名（results/<batch>/ 目录名）")
    parser.add_argument("--tasks", default=",".join(ALL_TASKS), help="逗号分隔任务名")
    parser.add_argument("--versions", default="", help="逗号分隔版本键（默认全部）")
    parser.add_argument("--rerun", default="", help="强制重跑的格子键（task::version，逗号分隔）")
    parser.add_argument("--timeout", type=int, default=780, help="单格看门狗秒数")
    args = parser.parse_args()

    sys.path.insert(0, str(HERE))
    from versions import VERSION_ORDER

    tasks = [t for t in args.tasks.split(",") if t]
    versions = [v for v in args.versions.split(",") if v] or VERSION_ORDER
    rerun = set(args.rerun.split(",")) - {""}

    results_dir = HERE / "results" / args.batch
    results_dir.mkdir(parents=True, exist_ok=True)
    preds_path = results_dir / "preds.json"
    preds = load_preds(preds_path)

    total_cost = sum(cell.get("instance_cost", 0.0) for cell in preds.values())
    print(f"batch={args.batch} cells={len(tasks) * len(versions)} 已有进度 {len(preds)} 格，累计成本 ${total_cost:.3f}")

    for task in tasks:
        for version in versions:
            key = f"{task}::{version}"
            cell_dir = results_dir / f"{task}__{version}"
            if key in preds and preds[key].get("status") == "done" and key not in rerun:
                print(f"[skip] {key}（已完成，judge={preds[key].get('judge')}）")
                continue
            print(f"[run ] {key} ...", flush=True)
            result = run_cell(task, version, cell_dir, args.timeout)
            if result.get("status") == "done":
                result.update(judge(task, cell_dir))
            else:
                result.update({"judge": "fail", "judge_note": f"status={result['status']}"})

            preds[key] = result
            save_preds(preds_path, preds)
            total_cost += result.get("instance_cost", 0.0)
            print(
                f"[done] {key}: status={result['status']} exit={result.get('exit_status')} "
                f"judge={result.get('judge')} steps={result.get('api_calls')} "
                f"cost=${result.get('instance_cost', 0):.3f} 累计=${total_cost:.3f}",
                flush=True,
            )

    # 汇总
    summary = {"batch": args.batch, "total_cost": round(total_cost, 3), "cells": preds}
    (results_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n==== 汇总（{len(preds)} 格，累计 ${total_cost:.3f}）====")
    for task in tasks:
        for version in versions:
            cell = preds.get(f"{task}::{version}", {})
            print(
                f"{task:26s} {version:15s} judge={cell.get('judge', '-'):6s} "
                f"steps={cell.get('api_calls', '-'):>4} cost=${cell.get('instance_cost', 0):.3f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
