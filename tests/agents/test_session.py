"""魔改 4: Session 持久化单元测试。

覆盖验收清单：逐事件落库正确、db_path 空=off 与上游一致、kill 中途 → 新 agent
从 DB 恢复 → 继续跑完（等价性）、kill 在响应与观察之间（悬挂 actions 本地重执行、
LM 不重烧）、压缩后中断恢复（含 _last_trigger_msg 的 seq 语义）、成本账本一致
（含计费 FormatError）、已完成会话拒绝恢复、轨迹注入 session_id。
"""

import json
from pathlib import Path

import pytest

from minisweagent.agents.default import DefaultAgent
from minisweagent.agents.session_store import SessionStore
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.test_models import DeterministicToolcallModel, make_toolcall_output

SUBMIT_CMD = "echo 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT' && echo done"


def tc_output(content: str, cmd: str, call_id: str) -> dict:
    return make_toolcall_output(
        content,
        [{"id": call_id, "type": "function", "function": {"name": "bash", "arguments": json.dumps({"command": cmd})}}],
        [{"command": cmd, "tool_call_id": call_id}],
    )


def make_config(db_path: str, **overrides) -> dict:
    config = {
        "system_template": "You are a test assistant.",
        "instance_template": "Task: {{task}}",
        "step_limit": 30,
        "cost_limit": 100.0,
        "session_db_path": db_path,
    }
    config |= overrides
    return config


def outputs_for(prefix: str, cmds: list[str], *, start: int = 0) -> list[dict]:
    return [
        tc_output(f"{prefix} step {start + i}", cmd, f"call_{start + i}")
        for i, cmd in enumerate(cmds)
    ]


class _Kill(BaseException):
    """Bypasses the run loop's ``except Exception`` — simulates SIGKILL exactly."""


# --- off = upstream byte-for-byte -------------------------------------------


def test_session_off_by_default(tmp_path: Path):
    """session_db_path 为空（默认）时不建库、不落任何东西，行为与上游一致。"""
    agent = DefaultAgent(
        model=DeterministicToolcallModel(outputs=outputs_for("x", [SUBMIT_CMD])),
        env=LocalEnvironment(),
        system_template="s",
        instance_template="t: {{task}}",
    )
    info = agent.run("task")
    assert info["exit_status"] == "Submitted"
    assert agent._session is None
    assert agent.session_id == ""
    assert agent._trajectory_extras() == ()
    # 没有任何文件被创建
    leftovers = [p for p in tmp_path.iterdir()]
    assert leftovers == []


def test_events_recorded(tmp_path: Path):
    """逐事件落库：行数、顺序、view_pos、完整 dict 无损、状态行、steps 视图。"""
    db = tmp_path / "sessions.db"
    cmds = ["echo one", "echo two", SUBMIT_CMD]
    agent = DefaultAgent(
        model=DeterministicToolcallModel(outputs=outputs_for("x", cmds)),
        env=LocalEnvironment(),
        **make_config(str(db)),
    )
    agent.run("recorded task")

    store = SessionStore(db)
    state = store.attach(agent.session_id)
    assert state["status"] == "finished"
    assert state["exit_status"] == "Submitted"
    assert state["n_calls"] == 3
    assert state["cost"] == pytest.approx(3.0)
    assert state["task"] == "recorded task"

    messages = store.load_messages()
    assert len(messages) == len(agent.messages)
    for restored, live in zip(messages, agent.messages):
        assert restored == live  # 完整 dict 无损往返

    # 成本账本：SUM(messages.cost) == agent.cost
    conn = store._conn
    total = conn.execute(
        "SELECT COALESCE(SUM(cost), 0) FROM messages WHERE session_id=? AND kind='message'",
        (agent.session_id,),
    ).fetchone()[0]
    assert total == pytest.approx(agent.cost)

    # v_steps 视图：每步 thought / tool_command / observation 可查
    steps = conn.execute(
        "SELECT thought, tool_command, observation FROM v_steps WHERE session_id=? ORDER BY step",
        (agent.session_id,),
    ).fetchall()
    assert len(steps) == 3
    assert steps[0]["tool_command"] == "echo one"
    assert "one" in steps[0]["observation"]

    # action_started 前置事件：每个动作一行，不进视图
    n_started = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id=? AND kind='action_started'",
        (agent.session_id,),
    ).fetchone()[0]
    assert n_started == 3
    store.close()


def test_trajectory_carries_session_id(tmp_path: Path):
    db = tmp_path / "sessions.db"
    out = tmp_path / "traj.json"
    agent = DefaultAgent(
        model=DeterministicToolcallModel(outputs=outputs_for("x", [SUBMIT_CMD])),
        env=LocalEnvironment(),
        **make_config(str(db), output_path=out),
    )
    agent.run("task")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["info"]["session_id"] == agent.session_id
    assert data["trajectory_format"] == "mini-swe-agent-1.1"


# --- kill & resume equivalence ----------------------------------------------


def _reference_run(cmds: list[str], db: Path, task: str = "kill resume task") -> tuple[dict, DefaultAgent]:
    """连续跑完的对照组。"""
    agent = DefaultAgent(
        model=DeterministicToolcallModel(outputs=outputs_for("x", cmds)),
        env=LocalEnvironment(),
        **make_config(str(db)),
    )
    return agent.run(task), agent


def _message_shapes(messages: list[dict]) -> list[tuple[str, str]]:
    """结构签名（忽略时间戳等不稳定字段）。"""
    shapes = []
    for m in messages:
        content = m.get("content")
        if not isinstance(content, str):
            content = json.dumps(content, sort_keys=True, default=str)
        shapes.append((m.get("role") or m.get("type", ""), content))
    return shapes


def test_kill_midway_resume_equivalent(tmp_path: Path):
    """kill 在第 2 步查询前 → 恢复后跑完；exit_status/成本/步数/消息结构与连续运行等价。"""
    cmds = ["echo one", "echo two", SUBMIT_CMD]
    ref_info, ref_agent = _reference_run(cmds, tmp_path / "ref.db")

    db = tmp_path / "kill.db"

    # 用 BaseException 在第 2 次 step query 时引爆，绕开 except Exception —— 精确模拟 SIGKILL
    class KillOnSecondModel(DeterministicToolcallModel):
        def query(self, messages, **kwargs):
            self.current_index += 1
            if self.current_index == 1:
                raise _Kill()
            return self.config.outputs[self.current_index]

    killed = DefaultAgent(
        model=KillOnSecondModel(outputs=outputs_for("x", cmds)),
        env=LocalEnvironment(),
        **make_config(str(db)),
    )
    with pytest.raises(_Kill):
        killed.run("kill resume task")
    # 内存里 n_calls=2（第二次调用在计数后才被 kill）；DB 里落的是最后一次提交的 1 —— 恢复以 DB 为准
    assert killed.n_calls == 2

    resumed = DefaultAgent(
        model=DeterministicToolcallModel(outputs=outputs_for("x", cmds[1:], start=1)),
        env=LocalEnvironment(),
        **make_config(str(db)),
    )
    info = resumed.resume(killed.session_id)

    assert info["exit_status"] == ref_info["exit_status"] == "Submitted"
    assert info["submission"] == ref_info["submission"]
    assert resumed.cost == pytest.approx(ref_agent.cost) == pytest.approx(3.0)
    assert resumed.n_calls == ref_agent.n_calls == 3
    assert _message_shapes(resumed.messages) == _message_shapes(ref_agent.messages)
    assert len(resumed.messages) == len(ref_agent.messages)


def test_kill_between_response_and_observation(tmp_path: Path):
    """kill 在 LM 响应落库之后、工具执行之前 → 恢复时本地重执行悬挂 actions，
    LM 不重烧（新模型的第一个输出必须对接第 2 步）。"""
    db = tmp_path / "kill2.db"
    ref_info, ref_agent = _reference_run(["echo one", SUBMIT_CMD], tmp_path / "ref2.db", task="dangling task")

    class KillInExecuteAgent(DefaultAgent):
        kill_armed = True

        def _execute_action(self, action):
            if self.kill_armed:
                self.kill_armed = False
                raise _Kill()
            return super()._execute_action(action)

    killed = KillInExecuteAgent(
        model=DeterministicToolcallModel(outputs=outputs_for("x", ["echo one", SUBMIT_CMD])),
        env=LocalEnvironment(),
        **make_config(str(db)),
    )
    with pytest.raises(_Kill):
        killed.run("dangling task")
    # 响应已落库，观察没有
    assert killed.messages[-1]["role"] == "assistant"

    resumed = KillInExecuteAgent(
        model=DeterministicToolcallModel(outputs=outputs_for("x", [SUBMIT_CMD], start=1)),
        env=LocalEnvironment(),
        **make_config(str(db)),
    )
    resumed.kill_armed = False
    info = resumed.resume(killed.session_id)

    assert info["exit_status"] == ref_info["exit_status"] == "Submitted"
    assert info["submission"] == ref_info["submission"]
    assert resumed.model.current_index == 0  # 恢复侧模型只烧了 submit 一步 —— LM 未重烧
    assert "one" in resumed.messages[3].get("content", "")  # 悬挂动作的观察在恢复时补上
    assert _message_shapes(resumed.messages) == _message_shapes(ref_agent.messages)
    assert resumed.n_calls == ref_agent.n_calls == 2


def test_resume_finished_session_rejected(tmp_path: Path):
    db = tmp_path / "done.db"
    agent = DefaultAgent(
        model=DeterministicToolcallModel(outputs=outputs_for("x", [SUBMIT_CMD])),
        env=LocalEnvironment(),
        **make_config(str(db)),
    )
    agent.run("task")
    with pytest.raises(ValueError, match="already finished"):
        DefaultAgent(
            model=DeterministicToolcallModel(outputs=[]),
            env=LocalEnvironment(),
            **make_config(str(db)),
        ).resume(agent.session_id)


def test_resume_requires_db_path():
    agent = DefaultAgent(
        model=DeterministicToolcallModel(outputs=[]),
        env=LocalEnvironment(),
        system_template="s",
        instance_template="t",
    )
    with pytest.raises(ValueError, match="session_db_path"):
        agent.resume("whatever")


# --- compression × persistence ----------------------------------------------


class _SummaryModel(DeterministicToolcallModel):
    """对压缩摘要调用（单条 user 消息、无 actions 上下文）返回固定五段摘要。"""

    def query(self, messages, **kwargs):
        if len(messages) == 1 and "folded_history" in str(messages[0].get("content", "")):
            return {
                "role": "assistant",
                "content": SUMMARY_TEXT,
                "extra": {"cost": 0.5, "actions": [], "timestamp": 0.0},
            }
        return super().query(messages, **kwargs)


SUMMARY_TEXT = """## 任务目标
求和。

## 已完成步骤
- 步骤 1: echo one

## 关键文件与产物路径
- /tmp/a.txt

## 当前计划与下一步
提交。

## 失败教训
无
"""


def test_compression_session_records_effective_view(tmp_path: Path):
    """压缩在 DB 中的镜像：被折叠行 superseded、事件行在位、视图与内存一致。"""
    db = tmp_path / "comp.db"
    big = "y" * 4000  # 触发 estimate 路径的压缩
    cmds = [f"echo {big}", "echo two", SUBMIT_CMD]
    agent = DefaultAgent(
        model=_SummaryModel(outputs=outputs_for("x", cmds)),
        env=LocalEnvironment(),
        **make_config(str(db), compression_threshold_tokens=500, compression_keep_recent_turns=1),
    )
    info = agent.run("compression task")
    assert info["exit_status"] == "Submitted"

    store = SessionStore(db)
    store.attach(agent.session_id)
    view = store.load_messages()
    assert view == agent.messages  # 当前有效视图 == 内存态（压缩后）
    n_superseded = store._conn.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id=? AND kind='message' AND view_pos IS NULL",
        (agent.session_id,),
    ).fetchone()[0]
    assert n_superseded > 0  # 原史仍在表里 = 全量事件流
    events = [m for m in view if "compression_event" in (m.get("extra") or {})]
    assert len(events) == 1
    store.close()


def test_compression_then_kill_resume(tmp_path: Path):
    """压缩后 kill → 恢复：视图含压缩事件、trigger 标记按 seq 还原、续跑到 Submitted、不重触发。"""
    db = tmp_path / "compkill.db"
    big = "y" * 4000
    cmds = [f"echo {big}", "echo two", SUBMIT_CMD]
    ref_agent = DefaultAgent(
        model=_SummaryModel(outputs=outputs_for("x", cmds)),
        env=LocalEnvironment(),
        **make_config(str(tmp_path / "ref3.db"), compression_threshold_tokens=500, compression_keep_recent_turns=1),
    )
    ref_info = ref_agent.run("compressed kill task")

    class KillOnThirdModel(_SummaryModel):
        def query(self, messages, **kwargs):
            if len(messages) == 1 and "folded_history" in str(messages[0].get("content", "")):
                return super().query(messages, **kwargs)
            self.current_index += 1
            if self.current_index == 2:  # 第 3 步查询：压缩已在本查询的 _maybe_compress 里发生
                raise _Kill()
            return self.config.outputs[self.current_index]

    killed = DefaultAgent(
        model=KillOnThirdModel(outputs=outputs_for("x", cmds)),
        env=LocalEnvironment(),
        **make_config(str(db), compression_threshold_tokens=500, compression_keep_recent_turns=1),
    )
    with pytest.raises(_Kill):
        killed.run("compressed kill task")
    # kill 发生在压缩之后的第 3 步查询里
    assert any("compression_event" in (m.get("extra") or {}) for m in killed.messages)
    # 内存里 n_calls=3（第三次调用在计数后被 kill）；DB 里最后一次提交的是 2
    assert killed.n_calls == 3

    resumed = DefaultAgent(
        model=_SummaryModel(outputs=outputs_for("x", cmds[2:], start=2)),
        env=LocalEnvironment(),
        **make_config(str(db), compression_threshold_tokens=500, compression_keep_recent_turns=1),
    )
    info = resumed.resume(killed.session_id)

    assert info["exit_status"] == "Submitted" == ref_info["exit_status"]
    assert any("compression_event" in (m.get("extra") or {}) for m in resumed.messages)
    assert resumed._compression_count == 1
    assert resumed.n_calls == 3
    # trigger 标记还原成内存对象（is 语义可用的前提）
    store = SessionStore(db)
    stored_pos = store.attach(resumed.session_id)["last_trigger_pos"]
    store.close()
    if stored_pos is not None:
        assert resumed._last_trigger_msg is resumed.messages[stored_pos]


# --- state cache / misc -------------------------------------------------------


def test_format_error_cost_persisted(tmp_path: Path):
    """计费 FormatError 的成本也进账本（恢复时重算 cost 不丢账）。"""
    from minisweagent.exceptions import FormatError
    from minisweagent.models import GLOBAL_MODEL_STATS

    class BilledErrorModel(DeterministicToolcallModel):
        def query(self, messages, **kwargs):
            self.current_index += 1
            if self.current_index < 2:
                GLOBAL_MODEL_STATS.add(1.0)
                raise FormatError(
                    {
                        "role": "user",
                        "content": "No tool calls found in the response.",
                        "extra": {"interrupt_type": "FormatError", "cost": 1.0},
                    }
                )
            return self.config.outputs[self.current_index]

    db = tmp_path / "fe.db"
    agent = DefaultAgent(
        model=BilledErrorModel(outputs=outputs_for("x", ["echo dummy", "echo dummy", SUBMIT_CMD])),
        env=LocalEnvironment(),
        **make_config(str(db), max_consecutive_format_errors=0),
    )
    info = agent.run("billed errors")
    assert info["exit_status"] == "Submitted"
    assert agent.cost == pytest.approx(3.0)  # 2 次计费错误 + 1 次成功

    store = SessionStore(db)
    total = store._conn.execute(
        "SELECT COALESCE(SUM(cost), 0) FROM messages WHERE session_id=? AND kind='message'",
        (agent.session_id,),
    ).fetchone()[0]
    assert total == pytest.approx(agent.cost)
    store.close()


def test_list_sessions(tmp_path: Path):
    db = tmp_path / "many.db"
    for i in range(2):
        agent = DefaultAgent(
            model=DeterministicToolcallModel(outputs=outputs_for("x", [SUBMIT_CMD])),
            env=LocalEnvironment(),
            **make_config(str(db)),
        )
        agent.run(f"task {i}")
    sessions = SessionStore.list_sessions(db)
    assert len(sessions) == 2
    assert all(s["status"] == "finished" for s in sessions)
    assert {s["task"] for s in sessions} == {"task 0", "task 1"}
