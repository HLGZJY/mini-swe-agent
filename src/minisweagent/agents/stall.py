"""魔改 5: stall (fake-progress) detection helpers.

清单要求（Harness啃法与魔改清单.md）：步数上限只是兜底，真正麻烦的是
「看起来一直在执行，但任务没有推进」。本模块是信号层：把每步的
（动作, 观察）归一化成一个步指纹，连续 ``window`` 步指纹不变 = 假性推进。

信号取舍（正面回答清单原文）：

- **「无新文件改动」被显式否决**：Environment 是每条命令独立 shell 的无状态
  接口，harness 对文件系统的唯一合法感知就是 action/observation 文本；主动
  扫盘快照既不可靠（写盘可发生在任意目录/容器）也越权（读工作目录之外）。
- **归一化抹平可变数字**：递增分页、时间戳、随机 id 在指纹层面不可见——
  这是刻意的。分页任务若输出内容在变，归一化后指纹仍然不同（内容骨架不同）；
  只有输出完全静态的 poll 会被计数，而那种 poll 连续 window 步也确实值得
  一次提醒（软干预，模型可以解释）。
- **已知边界**：数字规则会把 returncode 0→1 的翻转一并抹掉——失败重试与
  成功重试形似。但只要模型下一步做了不同的事，指纹立刻打破；误伤面收敛到
  「同一条命令连跑 window 步」，无论成败都值得提醒。
- **多 action 单步**：全部动作与全部观察一起进指纹（record separator 拼接），
  空步（无动作无观察）产生固定指纹——连续空步同样是假性推进。

阶梯状态机 :class:`StallDetector` 的三个标量整体进 ``_session_state()``（魔改 4
的 resume 边界）：恢复后计数还原，同一死循环不再白送满额窗口。窗口历史无需
落事件行——连续性检测只需要「连续相同指纹的步数」这一个标量，它就是滑动
窗口的等价压缩形式。

All functions in this module are pure except the detector, whose state is
exposed as plain scalars for persistence.
"""

import hashlib
import json
import re

from minisweagent.agents.compression import content_text

# 长十六进制/随机 token（commit hash、uuid 片段等），先于数字规则抹掉。
_LONG_TOKEN_RE = re.compile(r"\b[0-9a-fA-F][0-9a-fA-F_\-]{7,}\b")
# 引号内容（路径、字符串字面量里的可变值）整体归一。
_QUOTED_RE = re.compile(r"\"[^\"]*\"|'[^']*'")
# 剩余数字（页码、时间戳、计数器、returncode）。
_DIGITS_RE = re.compile(r"\d+")

_RECORD_SEP = "\x1e"  # ASCII record separator; never appears in model text.


def normalize_text(text: str) -> str:
    """Collapse mutable values (numbers, quoted strings, long hex tokens) so that
    structurally identical commands/observations hash equal."""
    if not text:
        return ""
    text = _QUOTED_RE.sub('"…"', text)
    text = _LONG_TOKEN_RE.sub("…", text)
    text = _DIGITS_RE.sub("#", text)
    return " ".join(text.split())


def action_signature(action: dict) -> str:
    """Normalized signature of one action dict (shell command or structured tool)."""
    if "command" in action:
        return normalize_text(str(action.get("command") or ""))
    tool = str(action.get("tool") or "?")
    try:
        args_text = json.dumps(action.get("args"), sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        args_text = str(action.get("args"))
    return f"{tool}({normalize_text(args_text)})"


def step_fingerprint(actions: list[dict] | None, observations: list[dict] | None) -> str:
    """One stable hash per step: normalized commands + normalized observations.

    Empty steps (no actions, no observations) hash to a fixed value on purpose —
    consecutive no-op steps are fake progress too.
    """
    parts = [action_signature(a) for a in (actions or [])]
    parts += [normalize_text(content_text(o.get("content"))) for o in (observations or [])]
    return hashlib.sha256(_RECORD_SEP.join(parts).encode("utf-8")).hexdigest()[:16]


WARNING_MILD = (
    "你可能卡住了：最近 {window} 步的命令与结果完全相同，任务没有推进。"
    "请重新规划你的方法，或如实说明当前障碍。"
)
WARNING_STRONG = (
    "停滞警告（第 {level} 次）：你再次连续 {window} 步重复完全相同的操作，任务没有推进。"
    "立即更换策略，或提交现有成果/如实终止任务。再次停滞将被强制终止。"
)


def warning_text(level: int, window: int) -> str:
    if level <= 1:
        return WARNING_MILD.format(window=window)
    return WARNING_STRONG.format(level=level, window=window)


class StallDetector:
    """阶梯状态机：连续 ``window`` 步同指纹 → strike。

    - strike 即重置 streak：每个完整窗口最多介入一次，节奏均匀。
    - warnings 只增不清零（保守设计）：防「装死 → 挪一步 → 再装死」绕过阶梯；
      代价是长会话里后续停滞直接从强提醒起步。假性推进的危害是烧钱，宁可偏严。
    - ``max_warnings`` 次提醒后，下一次 strike 直接终止（``max_warnings=0`` =
      不给提醒，首次停滞即终止——不建议，默认 2）。
    """

    def __init__(self, *, window: int, max_warnings: int):
        self.window = window
        self.max_warnings = max_warnings
        self.streak = 0
        self.last_fp: str | None = None
        self.warnings = 0

    def reset(self) -> None:
        self.streak = 0
        self.last_fp = None
        self.warnings = 0

    def to_state(self) -> dict:
        """Scalar state for the resume boundary (魔改 4 ``_session_state``)."""
        return {
            "stall_streak": self.streak,
            "stall_last_fp": self.last_fp,
            "stall_warnings": self.warnings,
        }

    def from_state(self, state: dict) -> None:
        self.streak = int(state.get("stall_streak", 0) or 0)
        fp = state.get("stall_last_fp")
        self.last_fp = str(fp) if fp else None
        self.warnings = int(state.get("stall_warnings", 0) or 0)

    def observe(self, fp: str) -> dict | None:
        """Feed one step fingerprint; return an intervention directive or ``None``.

        Directives: ``{"type": "warning", "level": n, "window": w}`` (inject a user
        message) or ``{"type": "terminate", ...}`` (raise StalledExceeded).
        """
        if fp == self.last_fp:
            self.streak += 1
        else:
            self.streak = 1
            self.last_fp = fp
            return None  # 新指纹 = 有推进
        if self.streak < self.window:
            return None
        self.streak = 0
        if self.warnings >= self.max_warnings:
            return {"type": "terminate", "warnings": self.warnings, "window": self.window}
        self.warnings += 1
        return {"type": "warning", "level": self.warnings, "window": self.window}
