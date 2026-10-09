"""P1 判定器：隐藏测试全绿 = solved。

用法：python checker.py --workdir <agent工作目录副本>
退出码：0 = solved；1 = fail。

判定性测试（bug 下必失败）：
- test_separator_prevents_concat_collision：值含下一 label 名的移位碰撞对（ab|instanceX vs abinstance|X）
- test_separator_protects_job_boundary：同类移位打在 instance/job 边界
其余为既有行为保持测试（修复不得破坏）。

"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HIDDEN_TEST = '''\
"""P1 隐藏验收：分隔符防拼接碰撞 + 既有行为保持。"""

from datetime import UTC, datetime, timedelta

import fingerprint as fp

T0 = datetime(2026, 10, 1, 9, 0, 0, tzinfo=UTC)
WINDOW = timedelta(minutes=10)


def test_separator_prevents_concat_collision():
    # 0xFF 分隔被去掉后，值里包含下一个 label 名即可移位碰撞：
    # 两者的无分隔拼接都是 "alertnameab" + "instanceinstanceX" + "jobj"
    a = fp.canonical_fingerprint({"alertname": "ab", "instance": "instanceX", "job": "j"}, T0)
    b = fp.canonical_fingerprint({"alertname": "abinstance", "instance": "X", "job": "j"}, T0)
    assert a != b, "label 段之间缺少分隔：ab|instanceX 与 abinstance|X 碰撞"


def test_separator_protects_job_boundary():
    # 同类移位打在 instance/job 边界：两者无分隔拼接都是 "instancey" + "jobjobobz"
    a = fp.canonical_fingerprint({"alertname": "q", "instance": "y", "job": "jobobz"}, T0)
    b = fp.canonical_fingerprint({"alertname": "q", "instance": "yjob", "job": "obz"}, T0)
    assert a != b, "instance/job 边界缺少分隔"


def test_same_input_still_stable():
    labels = {"alertname": "HighCPU", "instance": "10.0.0.1", "job": "node"}
    assert fp.canonical_fingerprint(labels, T0) == fp.canonical_fingerprint(labels, T0)


def test_window_semantics_kept():
    labels = {"alertname": "DiskFull", "instance": "10.0.0.2", "job": "node"}
    inside = fp.canonical_fingerprint(labels, T0 + timedelta(minutes=5))
    beyond = fp.canonical_fingerprint(labels, T0 + timedelta(minutes=11))
    assert inside != beyond
    # 候选指纹 = (当前桶, 前一桶)；前一桶应等于 T0-窗宽 的主指纹
    labels2 = {"alertname": "QueueDepth", "instance": "10.0.0.3", "job": "api"}
    at = T0 + timedelta(minutes=9, seconds=59)
    cur, prev = fp.candidate_fingerprints(labels2, at, WINDOW)
    assert prev == fp.canonical_fingerprint(labels2, at - WINDOW, WINDOW)
    assert cur == fp.canonical_fingerprint(labels2, at, WINDOW)


def test_missing_labels_ok():
    fp.canonical_fingerprint({"alertname": "SoleLabel"}, T0)
    fp.canonical_fingerprint({"alertname": "SoleLabel"}, T0 + timedelta(minutes=30))
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()
    workdir = Path(args.workdir).resolve()

    if not (workdir / "fingerprint.py").exists():
        print("RESULT: fail fingerprint.py 缺失")
        return 1

    with tempfile.TemporaryDirectory() as td:
        test_file = Path(td) / "test_hidden_p1.py"
        test_file.write_text(HIDDEN_TEST, encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(workdir)
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file), "-q", "--no-header", "-x"],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = out.strip().splitlines()[-1] if out.strip() else "(no output)"
    if proc.returncode == 0:
        print(f"RESULT: solved {tail}")
        return 0
    print(f"RESULT: fail {tail}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
