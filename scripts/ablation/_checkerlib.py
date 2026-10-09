"""checker 公共库：加载 workdir 模块 + 统一判定输出（solved/fail）。

约定：checker.py 末尾调用 ``main(tests)``；tests 是 ``list[tuple[str, callable]]``，
每个 callable 收到已加载的模块 dict（按文件名去 .py）并 assert。
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path


def load_workdir_modules(workdir: Path) -> dict[str, object]:
    """把 workdir 下每个非测试 .py 加载为模块（按文件名注册，互不依赖 import 机制）。

    test_*.py 跳过（公开测试不是判定的被测对象，且其顶层 import 会因加载顺序炸）；
    workdir 同时压入 sys.path，兼容 agent 解法里的模块内互相 import。
    """
    sys.path.insert(0, str(workdir))
    mods: dict[str, object] = {}
    for py in sorted(workdir.glob("*.py")):
        if py.stem.startswith("test_"):
            continue
        spec = importlib.util.spec_from_file_location(py.stem, py)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[py.stem] = mod
        spec.loader.exec_module(mod)
        mods[py.stem] = mod
    return mods


def workdir_from_argv() -> Path:
    args = sys.argv
    return Path(args[args.index("--workdir") + 1]).resolve()


def main(tests: list[tuple[str, object]]) -> None:
    workdir = workdir_from_argv()
    mods = load_workdir_modules(workdir)
    passed = 0
    failed: list[str] = []
    for name, fn in tests:
        try:
            fn(mods)
            passed += 1
        except Exception:  # noqa: BLE001 —— 判定器收集全部失败后统一裁决
            failed.append(f"{name}: {traceback.format_exc(limit=2)}")
    if failed:
        print(f"RESULT: fail {passed} passed, {len(failed)} failed")
        for f in failed:
            print("  -", f.splitlines()[-1][:160])
        raise SystemExit(1)
    print(f"RESULT: solved {passed} passed")
    raise SystemExit(0)
