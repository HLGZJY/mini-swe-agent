"""魔改 5: 停滞检测（防死循环）单元测试。

覆盖验收清单：
- stall.py 纯函数：归一化规则、指纹等值性（装死相同 / 分页不同）、阶梯状态机语义
- agent 集成：装死阶梯介入（提醒 role/位置/extra → 强提醒 → StalledExceeded）、
  正常任务零误伤、stall_window=0 与上游逐字一致、提醒后挽回路径
- 魔改 4 交互：kill 中途 → resume → streak/warnings 计数还原（不再白送满额窗口）、
  悬挂重执行不喂检测器、stall_event/exit_status 可 SQL 统计
- SessionStore v1 → v2 幂等迁移
"""

import json
import sqlite3
from pathlib import Path

import pytest

from minisweagent.agents.default import DefaultAgent
from minisweagent.agents.session_store import SessionStore
from minisweagent.agents.stall import (
    StallDetector,
    action_signature,
    normalize_text,
    step_fingerprint,
    warning_text,
)
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.test_models import DeterministicToolcallModel, make_toolcall_output

SUBMIT_CMD = "echo 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT' && echo done"
STUCK_CMD = "echo stuck"


def tc_output(content: str, cmd: str, call_id: str) -> dict:
    return make_toolcall_output(
        content,
        [{"id": call_id, "type": "function", "function": {"name": "bash", "arguments": json.dumps({"command": cmd})}}],
        [{"command": cmd, "tool_call_id": call_id}],
    )


def stuck_outputs(n: int, *, submit_at_end: bool = False) -> list[dict]:
    """n identical fake-progress steps, optionally a submit at the very end."""
    outs = [tc_output(f"step {i}", STUCK_CMD, f"call_{i}") for i in range(n)]
    if submit_at_end:
        outs.append(tc_output("finally submitting", SUBMIT_CMD, f"call_{n}"))
    return outs


def make_config(**overrides) -> dict:
    config = {
        "system_template": "You are a test assistant.",
        "instance_template": "Task: {{task}}",
        "step_limit": 60,
        "cost_limit": 500.0,
    }
    config |= overrides
    return config


def stall_events(messages: list[dict]) -> list[dict]:
    return [(m.get("extra") or {}).get("stall_event") for m in messages if (m.get("extra") or {}).get("stall_event")]


class _Kill(BaseException):
    """Bypasses the run loop's ``except Exception`` — simulates SIGKILL exactly."""


# --- stall.py 纯函数 ---------------------------------------------------------


def test_normalize_text_rules():
    assert normalize_text("git log deadbeefcafe") == "git log …"
    assert normalize_text('echo "hello world" 123') == 'echo "…" #'
    assert normalize_text("curl 'api?v=2&page=10'") == 'curl "…"'
    assert normalize_text("ls   -la\t/tmp") == "ls -la /tmp"
    assert normalize_text("") == ""
    # 短 token 不触发 hex 规则（>=8 位才动）；数字 123 仍被数字规则抹掉
    assert normalize_text("abc123") == "abc#"
    assert normalize_text("deadc0de") == "…"  # 全 hex 8 位 → 抹


def test_action_signature_shell_and_tool():
    assert action_signature({"command": "echo 1"}) == "echo #"
    sig = action_signature({"tool": "read_file", "args": {"path": "a.txt", "offset": 5}})
    assert sig.startswith("read_file(") and "#" in sig


def test_fingerprint_identical_repeat_and_static_poll():
    obs1 = [{"content": "<returncode>0</returncode><output>status: ok at 12:00:01</output>"}]
    obs2 = [{"content": "<returncode>0</returncode><output>status: ok at 12:00:02</output>"}]
    actions = [{"command": "check status"}]
    # 静态 poll：只有时间戳数字变化 → 指纹相同（已知边界，行为锁定）
    assert step_fingerprint(actions, obs1) == step_fingerprint(actions, obs2)
    # 同命令同观察 → 相同；不同命令 → 不同
    assert step_fingerprint(actions, obs1) == step_fingerprint(actions, obs1)
    assert step_fingerprint(actions, obs1) != step_fingerprint([{"command": "check other"}], obs1)


def test_fingerprint_paging_not_stall():
    """递增分页：命令归一后相同，但观察内容骨架不同 → 指纹不同（核心反误报用例）。"""
    a1 = [{"command": "curl api page=1"}]
    a2 = [{"command": "curl api page=2"}]
    o1 = [{"content": "<output>items: alpha beta</output>"}]
    o2 = [{"content": "<output>items: gamma delta</output>"}]
    assert step_fingerprint(a1, o1) != step_fingerprint(a2, o2)
    assert step_fingerprint(a1, o1) != step_fingerprint(a1, o2)  # 同命令不同内容也不算停滞


def test_fingerprint_empty_step_is_fixed_value():
    assert step_fingerprint([], []) == step_fingerprint(None, None)
    assert step_fingerprint([], []) != step_fingerprint([{"command": "x"}], [])


def test_fingerprint_multi_action_step():
    two = [{"command": "echo a"}, {"command": "echo b"}]
    one = [{"command": "echo a"}]
    obs = [{"content": "o1"}, {"content": "o2"}]
    assert step_fingerprint(two, obs) != step_fingerprint(one, obs[:1])


def test_detector_ladder_semantics():
    """window=3, max_warnings=2：strike 落在 3/6/9；strike 清 streak；warnings 只增。"""
    d = StallDetector(window=3, max_warnings=2)
    assert d.observe("fp") is None  # streak=1
    assert d.observe("fp") is None  # streak=2
    assert d.observe("fp") == {"type": "warning", "level": 1, "window": 3}  # strike1, streak→0
    assert d.streak == 0 and d.warnings == 1
    # strike 后继续同指纹：重新数满一个窗口
    assert d.observe("fp") is None  # streak=1
    assert d.observe("fp") is None  # streak=2
    assert d.observe("fp") == {"type": "warning", "level": 2, "window": 3}  # strike2
    # 指纹打破：streak 重新计，但 warnings 保留（防「挪一步再装死」绕过阶梯）
    assert d.observe("fp2") is None
    assert d.streak == 1 and d.warnings == 2
    assert d.observe("fp2") is None
    assert d.observe("fp2") == {"type": "terminate", "warnings": 2, "window": 3}


def test_detector_state_roundtrip():
    d = StallDetector(window=3, max_warnings=2)
    d.observe("fp")
    d.observe("fp")
    d.observe("fp")  # strike1
    d.observe("fp")
    state = d.to_state()
    assert state == {"stall_streak": 1, "stall_last_fp": "fp", "stall_warnings": 1}
    d2 = StallDetector(window=3, max_warnings=2)
    d2.from_state(state)
    assert d2.streak == 1 and d2.last_fp == "fp" and d2.warnings == 1
    # 还原后继续：streak 2→3 触发 strike2（证明计数真正接续）
    assert d2.observe("fp") is None
    assert d2.observe("fp") == {"type": "warning", "level": 2, "window": 3}


def test_detector_zero_warnings_terminates_on_first_strike():
    d = StallDetector(window=2, max_warnings=0)
    assert d.observe("fp") is None
    assert d.observe("fp") == {"type": "terminate", "warnings": 0, "window": 2}


def test_warning_text_escalation():
    assert "重新规划" in warning_text(1, 3)
    assert "第 2 次" in warning_text(2, 3) and "强制终止" in warning_text(2, 3)


# --- agent 集成 ---------------------------------------------------------------


def test_stall_off_by_default():
    """stall_window 缺省 0：装死 N 步不触发任何介入，行为与上游一致。"""
    agent = DefaultAgent(
        model=DeterministicToolcallModel(outputs=stuck_outputs(5, submit_at_end=True)),
        env=LocalEnvironment(),
        **make_config(),
    )
    info = agent.run("off-by-default task")
    assert info["exit_status"] == "Submitted"
    assert stall_events(agent.messages) == []


def test_stall_ladder_full_escalation():
    """装死到底：window=2 → 温和提醒 → 强提醒 → StalledExceeded 终止。"""
    agent = DefaultAgent(
        model=DeterministicToolcallModel(outputs=stuck_outputs(10)),
        env=LocalEnvironment(),
        **make_config(stall_window=2, stall_max_warnings=2),
    )
    info = agent.run("stuck task")
    assert info["exit_status"] == "StalledExceeded"
    assert info["submission"] == ""
    events = stall_events(agent.messages)
    assert [e["type"] for e in events] == ["warning", "warning", "terminate"]
    assert [e["level"] for e in events[:2]] == [1, 2]
    # 提醒注入语义：role=user、位于对应观察之后、extra 可追溯
    warnings = [m for m in agent.messages if m.get("role") == "user" and (m.get("extra") or {}).get("stall_event")]
    assert len(warnings) == 2
    assert "重新规划" in warnings[0]["content"]
    assert "强制终止" in warnings[1]["content"]
    # 节奏：每 2 步一次 strike（步 2、4、6）
    view_positions = [i for i, m in enumerate(agent.messages) if m is warnings[0]]
    assert view_positions[0] > 4  # system, instance, asst1, obs1 之后
    assert agent._stall.warnings == 2


def test_normal_task_zero_false_positive():
    """交替命令的正常任务：检测器全程沉默，正常提交。"""
    cmds = ["echo one", "echo two", "echo one", "echo two", SUBMIT_CMD]
    outs = [tc_output(f"step {i}", c, f"call_{i}") for i, c in enumerate(cmds)]
    agent = DefaultAgent(
        model=DeterministicToolcallModel(outputs=outs),
        env=LocalEnvironment(),
        **make_config(stall_window=2, stall_max_warnings=2),
    )
    info = agent.run("normal task")
    assert info["exit_status"] == "Submitted"
    assert stall_events(agent.messages) == []


def test_warning_recovery_path():
    """提醒后模型换策略（指纹打破）→ 挽回，任务正常完成。"""
    outs = [
        tc_output("s0", STUCK_CMD, "c0"),
        tc_output("s1", STUCK_CMD, "c1"),  # 步 2 → strike1, warning1
        tc_output("s2", "echo changed strategy", "c2"),  # 打破指纹
        tc_output("s3", SUBMIT_CMD, "c3"),
    ]
    agent = DefaultAgent(
        model=DeterministicToolcallModel(outputs=outs),
        env=LocalEnvironment(),
        **make_config(stall_window=2, stall_max_warnings=2),
    )
    info = agent.run("recovery task")
    assert info["exit_status"] == "Submitted"
    events = stall_events(agent.messages)
    assert [e["type"] for e in events] == ["warning"]  # 一次提醒，挽回
    assert events[0]["level"] == 1


def test_stall_event_sql_queryable(tmp_path: Path):
    """验收：stall 事件 / exit_status 能从 DB 直接 SQL 统计（魔改 6 备料）。"""
    db = tmp_path / "stall.db"
    agent = DefaultAgent(
        model=DeterministicToolcallModel(outputs=stuck_outputs(10)),
        env=LocalEnvironment(),
        **make_config(stall_window=2, stall_max_warnings=2, session_db_path=str(db)),
    )
    agent.run("sql task")
    conn = sqlite3.connect(str(db))
    try:
        counts = dict(
            conn.execute(
                "SELECT json_extract(data, '$.extra.stall_event.type'), count(*)"
                " FROM messages WHERE kind = 'message' AND data LIKE '%stall_event%'"
                " GROUP BY 1"
            ).fetchall()
        )
        assert counts == {"warning": 2, "terminate": 1}
        exit_status, stall_warnings = conn.execute(
            "SELECT exit_status, stall_warnings FROM sessions WHERE session_id = ?", (agent.session_id,)
        ).fetchone()
        assert exit_status == "StalledExceeded"
        assert stall_warnings == 2
    finally:
        conn.close()


# --- kill → resume：计数还原（魔改 4 交互核心） ---------------------------------


def test_kill_midway_streak_and_warnings_restored(tmp_path: Path):
    """kill 中途 → resume：streak 与 warnings 标量还原，死循环不再白送满额窗口。

    编排（window=3）：步1-2 装死（streak=2 落库）→ 步3 query 时 kill；
    resume 后第 1 个 LM 步即触发 warning1（证明 streak=2 接续），再装死
    一个窗口触发 warning2（证明 warnings=1 接续——若归零这里只会是 level 1）。
    """
    db = tmp_path / "kill.db"

    class KillOnThirdModel(DeterministicToolcallModel):
        def query(self, messages, **kwargs):
            self.current_index += 1
            if self.current_index == 2:
                raise _Kill()
            return self.config.outputs[self.current_index]

    killed = DefaultAgent(
        model=KillOnThirdModel(outputs=stuck_outputs(5)),
        env=LocalEnvironment(),
        **make_config(stall_window=3, stall_max_warnings=2, session_db_path=str(db)),
    )
    with pytest.raises(_Kill):
        killed.run("stall resume task")
    assert killed._stall.streak == 2 and killed._stall.warnings == 0

    resumed = DefaultAgent(
        model=DeterministicToolcallModel(outputs=stuck_outputs(8)),
        env=LocalEnvironment(),
        **make_config(stall_window=3, stall_max_warnings=2, session_db_path=str(db)),
    )
    resumed.resume(killed.session_id)  # 跑完整阶梯到 terminate 才返回
    events = stall_events(resumed.messages)
    # 还原证据 1（streak）：warning1 出现在 resume 恢复点后的第 1 个 LM 步——
    # killed 侧已落库 6 条消息（sys/instance/asst1/obs1/asst2/obs2），streak=2 接续，
    # 本地步 1 即 streak=3 触发 strike1（消息序 = asst, obs, warning）；若窗口归零，
    # warning1 要等到本地第 3 步（index 12 之后）。
    n_restored = len(killed.messages)
    first_warning_idx = next(
        i for i, m in enumerate(resumed.messages) if (m.get("extra") or {}).get("stall_event")
    )
    assert first_warning_idx == n_restored + 2
    # 还原证据 2（warnings）：后续阶梯从 level 2 起步而非重新从 level 1
    assert [e["type"] for e in events] == ["warning", "warning", "terminate"]
    assert [e["level"] for e in events[:2]] == [1, 2]
    assert resumed.messages[-1]["extra"]["exit_status"] == "StalledExceeded"


def test_kill_between_response_and_observation_not_counted(tmp_path: Path):
    """悬挂 actions 的 resume 本地重执行不喂检测器：重执行不产生新 LM 响应，
    streak 只在 _run_loop 的新步累计——warning1 恰好出现在预期位置。

    编排（window=3）：步1 装死完成（streak=1）→ 步2 响应落库后、工具执行前 kill；
    resume 重执行步2 的悬挂观察（不计），此后步3 streak=2、步4 streak=3 → warning1。
    """
    db = tmp_path / "kill2.db"

    class KillInExecuteAgent(DefaultAgent):
        executes = 0

        def _execute_action(self, action):
            self.executes += 1
            if self.executes == 2:  # 步 2 执行时 kill：步 1 已完整落库（streak=1）
                raise _Kill()
            return super()._execute_action(action)

    killed = KillInExecuteAgent(
        model=DeterministicToolcallModel(outputs=stuck_outputs(6)),
        env=LocalEnvironment(),
        **make_config(stall_window=3, stall_max_warnings=2, session_db_path=str(db)),
    )
    with pytest.raises(_Kill):
        killed.run("dangling stall task")
    assert killed.messages[-1]["role"] == "assistant"  # 响应已落库，观察没有
    assert killed._stall.streak == 1

    resumed = KillInExecuteAgent(
        model=DeterministicToolcallModel(outputs=stuck_outputs(8)),
        env=LocalEnvironment(),
        **make_config(stall_window=3, stall_max_warnings=2, session_db_path=str(db)),
    )
    resumed.executes = 100  # resume 侧不再触发 kill（类里按执行次数引爆）
    info = resumed.resume(killed.session_id)  # 恢复后继续跑完整阶梯到 terminate
    assert info["exit_status"] == "StalledExceeded"
    # 悬挂动作的观察在 resume 时补上（index 5，紧跟 5 条已落库消息）
    assert resumed.messages[5].get("role") == "tool" and "stuck" in str(resumed.messages[5].get("content"))
    events = stall_events(resumed.messages)
    assert [e["type"] for e in events] == ["warning", "warning", "terminate"]
    # 还原证据（streak）：killed 侧步 1 已计 streak=1，重执行补观察不计步 →
    # 本地步 2（恢复点后第 2 个 LM 步）即 streak=3 触发 strike1；若窗口归零，
    # strike1 要推迟一个 LM 步。warning1 的消息序：5 条恢复 + 重执行观察(1)
    # + 步3 asst/obs(2) + 步4 asst/obs(2) → index 10；归零时为 12。
    first_warning_idx = next(
        i for i, m in enumerate(resumed.messages) if (m.get("extra") or {}).get("stall_event")
    )
    assert first_warning_idx == 10
    assert [e["level"] for e in events[:2]] == [1, 2]


# --- SessionStore v1 → v2 迁移 -------------------------------------------------


def test_v1_db_migration_adds_stall_columns(tmp_path: Path):
    """v1 旧库打开：自动补三列，旧行可读，attach 后 stall 标量取默认值。"""
    db = tmp_path / "v1.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        PRAGMA user_version = 1;
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY, created_at REAL NOT NULL, updated_at REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'running', exit_status TEXT NOT NULL DEFAULT '',
            submission TEXT NOT NULL DEFAULT '', task TEXT NOT NULL DEFAULT '',
            cost REAL NOT NULL DEFAULT 0.0, n_calls INTEGER NOT NULL DEFAULT 0,
            n_consecutive_format_errors INTEGER NOT NULL DEFAULT 0,
            start_time_epoch REAL NOT NULL DEFAULT 0.0, compression_count INTEGER NOT NULL DEFAULT 0,
            last_trigger_pos INTEGER, extra_template_vars TEXT NOT NULL DEFAULT '{}',
            cost_last_confirmed REAL NOT NULL DEFAULT 0.0, agent_type TEXT NOT NULL DEFAULT '',
            agent_version TEXT NOT NULL DEFAULT '', config_snapshot TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE messages (
            session_id TEXT NOT NULL, seq INTEGER NOT NULL, view_pos INTEGER,
            kind TEXT NOT NULL DEFAULT 'message', role TEXT NOT NULL DEFAULT '',
            cost REAL NOT NULL DEFAULT 0.0, data TEXT NOT NULL, created_at REAL NOT NULL,
            PRIMARY KEY (session_id, seq)
        );
        INSERT INTO sessions (session_id, created_at, updated_at, task)
        VALUES ('old-session', 1.0, 1.0, 'legacy');
        """
    )
    conn.commit()
    conn.close()

    store = SessionStore(str(db))  # 打开即迁移
    cols = {r[1] for r in store._conn.execute("PRAGMA table_info(sessions)")}
    assert {"stall_streak", "stall_last_fp", "stall_warnings"} <= cols
    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == 2
    state = store.attach("old-session")
    assert int(state.get("stall_streak", 0)) == 0
    assert state["task"] == "legacy"
    store.close()

    # 幂等：再次打开不炸、不再重复加列
    store2 = SessionStore(str(db))
    assert store2._conn.execute("PRAGMA user_version").fetchone()[0] == 2
    store2.close()


def test_new_session_persists_stall_scalars(tmp_path: Path):
    """create_session/update_state 全链路写入 stall 三列。"""
    db = tmp_path / "scalars.db"
    agent = DefaultAgent(
        model=DeterministicToolcallModel(outputs=stuck_outputs(4, submit_at_end=True)),
        env=LocalEnvironment(),
        **make_config(stall_window=3, stall_max_warnings=2, session_db_path=str(db)),
    )
    info = agent.run("scalars task")
    assert info["exit_status"] == "Submitted"
    conn = sqlite3.connect(str(db))
    try:
        streak, warnings, fp = conn.execute(
            "SELECT stall_streak, stall_warnings, stall_last_fp FROM sessions WHERE session_id = ?",
            (agent.session_id,),
        ).fetchone()
    finally:
        conn.close()
    # 步1-3 装死 → strike1（streak 清 0，warnings=1）；步4 streak=1；步5 submit 打破指纹
    assert warnings == 1 and streak == 1 and fp is not None
    assert len(stall_events(agent.messages)) == 1
