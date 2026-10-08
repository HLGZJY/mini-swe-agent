"""魔改 3: Context 压缩单元测试。

覆盖验收清单：阈值触发/不触发、0=off 与上游逐字一致、压缩后 tool_calls 配对合法、
保留最近 N 轮、LM 摘要与机械兜底（含 FormatError 两条恢复路径）、摘要成本记账、
陈旧 usage 不重触发、二次压缩、快照落盘、轨迹事件字段。
"""

import json
from pathlib import Path

import pytest

from minisweagent.agents.compression import (
    SUMMARY_SECTION_HEADERS,
    build_folded_transcript,
    content_from_persisted_response,
    estimate_tokens,
    find_cut_index,
    last_prompt_usage,
    mechanical_summary,
    validate_summary,
)
from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.exceptions import FormatError
from minisweagent.models import GLOBAL_MODEL_STATS
from minisweagent.models.test_models import DeterministicToolcallModel, make_output, make_toolcall_output

BIG = "x" * 2000
"""Long observation payload so folded regions reliably dwarf the summary (shrink guard)."""

SUMMARY_TEXT = """## 任务目标
把三个数字文件的内容求和。

## 已完成步骤
- 步骤 1: 读取 a.txt
- 步骤 2: 读取 b.txt

## 关键文件与产物路径
- /tmp/a.txt
- /tmp/b.txt

## 当前计划与下一步
读取所有文件后用 bash 求和并提交。

## 失败教训
无
"""


def tc_output(content: str, cmd: str, call_id: str, usage_tokens: int | None = None) -> dict:
    """A bash toolcall turn; optionally carries a real usage signal in extra.response."""
    out = make_toolcall_output(
        content,
        [{"id": call_id, "type": "function", "function": {"name": "bash", "arguments": json.dumps({"command": cmd})}}],
        [{"command": cmd, "tool_call_id": call_id}],
    )
    if usage_tokens is not None:
        out["extra"]["response"] = {"usage": {"prompt_tokens": usage_tokens}}
    return out


def make_agent(outputs: list[dict] | None = None, *, model=None, **overrides) -> DefaultAgent:
    config = {
        "system_template": "You are a test assistant.",
        "instance_template": "Task: {{task}}",
        "step_limit": 30,
        "cost_limit": 100.0,
    }
    config |= overrides
    if model is None:
        model = DeterministicToolcallModel(outputs=outputs or [])
    return DefaultAgent(model=model, env=LocalEnvironment(), **config)


def compression_events(messages: list[dict]) -> list[dict]:
    return [m["extra"]["compression_event"] for m in messages if "compression_event" in (m.get("extra") or {})]


def assert_tool_calls_paired(messages: list[dict]) -> None:
    """Every assistant tool_calls is immediately followed by its tool results — except the
    upstream submit turn, which ends the run with an exit message and no observations."""
    for i, msg in enumerate(messages[:-1]):
        if msg.get("role") != "assistant" or not msg.get("tool_calls"):
            continue
        ids = {tc["id"] for tc in msg["tool_calls"]}
        seen = set()
        j = i + 1
        while j < len(messages) and messages[j].get("role") == "tool":
            seen.add(messages[j]["tool_call_id"])
            j += 1
        if not seen and any(m.get("role") == "exit" for m in messages[i + 1 :]):
            continue
        assert seen == ids, f"tool_calls pairing broken at message {i}"


# --- compression.py pure functions ---


def test_find_cut_index_respects_keep_recent_turns():
    messages = [{"role": "system"}, {"role": "user"}]
    for k in range(5):
        messages += [{"role": "assistant", "content": f"a{k}"}, {"role": "tool", "content": f"t{k}"}]
    # starts = [2, 4, 6, 8, 10]; keep the last 2 turns -> fold [2:8), cut at 8
    assert find_cut_index(messages, keep_recent_turns=2) == 8
    assert find_cut_index(messages, keep_recent_turns=4) == 4
    assert find_cut_index(messages, keep_recent_turns=5) is None  # nothing foldable
    assert find_cut_index([{"role": "system"}, {"role": "user"}], 2) is None


def test_find_cut_index_only_cuts_at_assistant_starts():
    messages = [{"role": "system"}, {"role": "user"}, {"role": "user", "content": "format error orphan"}]
    messages += [{"role": "assistant", "content": "a"}, {"role": "tool", "content": "t"}] * 3
    cut = find_cut_index(messages, keep_recent_turns=2)
    assert cut is not None and messages[cut]["role"] == "assistant"


def test_estimate_tokens_and_last_prompt_usage():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": "hello world", "extra": {"response": {"usage": {"prompt_tokens": 123}}}},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "second", "extra": {"response": "unserializable-repr-fallback"}},
    ]
    assert estimate_tokens(messages, 4.0) >= 1
    # scans backwards past newer assistants without a dict response, returns the last
    # assistant that carries a real usage record (stale-but-real beats an estimate)
    assert last_prompt_usage(messages) == (1, 123)
    messages[-1]["extra"]["response"] = {"usage": {"prompt_tokens": 456}}
    assert last_prompt_usage(messages) == (3, 456)  # type: ignore[misc]
    assert last_prompt_usage([{"role": "user", "content": "x"}]) is None


def test_validate_summary_requires_all_sections_in_order():
    assert validate_summary(SUMMARY_TEXT)
    assert not validate_summary(SUMMARY_TEXT.replace("## 失败教训", "## lessons"))
    reordered = "\n".join(reversed(SUMMARY_SECTION_HEADERS))
    assert not validate_summary(reordered)
    assert not validate_summary("")


def test_content_from_persisted_response():
    response = {"choices": [{"message": {"role": "assistant", "content": SUMMARY_TEXT}}]}
    assert content_from_persisted_response(response) == SUMMARY_TEXT
    assert content_from_persisted_response({"choices": [{"message": {"content": None}}]}) is None
    assert content_from_persisted_response({"no": "choices"}) is None
    assert content_from_persisted_response("repr-string-fallback") is None
    assert content_from_persisted_response(None) is None


def test_build_folded_transcript_roles_and_truncation():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"function": {"name": "bash", "arguments": '{"command": "ls"}'}}],
        },
        {"role": "tool", "content": BIG},
        {"role": "assistant", "content": "next"},
    ]
    transcript = build_folded_transcript(messages, 4, max_msg_chars=100)
    assert "[assistant]" in transcript and "bash" in transcript and "[tool]" in transcript
    assert "(no text)" in transcript and "truncated" in transcript
    assert transcript.count(BIG) == 0  # truncated away


def test_mechanical_summary_sections_and_error_events():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Find the answer in /tmp/data/results.csv"},
        {"role": "assistant", "content": "reading file", "tool_calls": []},
        {
            "role": "tool",
            "content": f"cat /tmp/data/notes.md\n{BIG}",
            "extra": {"error_class": "tool_error", "returncode": 1, "exception_info": "boom"},
        },
        {"role": "assistant", "content": "retrying now"},
    ]
    summary = mechanical_summary(messages, 5, task="do the thing")  # fold everything after [sys, user]
    for header in SUMMARY_SECTION_HEADERS:
        assert header in summary
    assert "do the thing" in summary  # task goal
    assert "reading file" in summary  # step list
    assert "/tmp/data/notes.md" in summary  # artifact path salvaged
    assert "error_class=tool_error" in summary  # 魔改 2 error channel reused
    assert "retrying now" in summary  # last assistant = plan proxy


# --- agent integration ---


def test_compression_disabled_matches_upstream_behavior():
    outputs = [
        tc_output("step 0", f"echo step0-{BIG}", "c0"),
        tc_output("done", "echo 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT' && echo finished", "c1"),
    ]
    agent = make_agent(outputs)  # no compression kwargs at all
    info = agent.run("plain task")
    assert info["exit_status"] == "Submitted"
    assert compression_events(agent.messages) == []
    assert agent.messages[0]["role"] == "system" and agent.messages[1]["role"] == "user"
    assert agent.messages[2]["role"] == "assistant"  # no event message inserted


def test_no_trigger_below_threshold():
    outputs = [tc_output(f"step {i}", f"echo step{i}-{BIG}", f"c{i}", usage_tokens=100) for i in range(3)] + [
        tc_output("done", "echo 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT' && echo finished", "cend")
    ]
    agent = make_agent(outputs, compression_threshold_tokens=5000, compression_keep_recent_turns=2)
    info = agent.run("small task")
    assert info["exit_status"] == "Submitted"
    assert compression_events(agent.messages) == []
    assert agent.n_calls == 4


def test_compression_triggers_and_task_still_completes(reset_global_stats):
    outputs = (
        [
            tc_output(f"step {i}", f"echo step{i}-{BIG}", f"c{i}", usage_tokens=9000 if i == 2 else None)
            for i in range(3)
        ]
        + [make_output(SUMMARY_TEXT, [])]
        + [tc_output(f"post {i}", f"echo post{i}-{BIG}", f"p{i}") for i in range(2)]
        + [tc_output("done", "echo 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT' && echo finished", "cend")]
    )
    agent = make_agent(
        outputs,
        compression_threshold_tokens=5000,
        compression_keep_recent_turns=2,
        output_path=None,
    )
    info = agent.run("sum numbers")
    assert info["exit_status"] == "Submitted" and info["submission"].strip() == "finished"

    events = compression_events(agent.messages)
    assert len(events) == 1  # stale usage in kept turns must NOT re-trigger
    event = events[0]
    assert event["trigger"] == {"source": "usage", "tokens_before": 9000}
    assert event["summary_source"] == "lm"
    assert event["folded_message_count"] == 2  # exactly one full turn folded
    assert event["kept_messages"] == 4
    assert "tokens_after_estimate" in event

    event_msg = agent.messages[2]
    assert event_msg["role"] == "user" and "历史压缩" in event_msg["content"]
    assert "## 失败教训" in event_msg["content"]
    # never-folded prefix intact
    assert agent.messages[0]["role"] == "system"
    assert agent.messages[1]["role"] == "user" and "sum numbers" in agent.messages[1]["content"]
    assert_tool_calls_paired(agent.messages)

    # cost accounting: 6 step queries + 1 summary call, all at 1.0
    assert agent.cost == pytest.approx(7.0)
    assert agent.cost == pytest.approx(GLOBAL_MODEL_STATS.cost)


def test_second_compression_on_fresh_usage_signal():
    outputs = (
        [
            tc_output(f"step {i}", f"echo step{i}-{BIG}", f"c{i}", usage_tokens=9000 if i == 2 else None)
            for i in range(3)
        ]
        + [make_output(SUMMARY_TEXT, [])]
        + [
            tc_output("step 3", f"echo step3-{BIG}", "c3"),
            tc_output("step 4", f"echo step4-{BIG}", "c4", usage_tokens=9000),
        ]
        + [make_output(SUMMARY_TEXT.replace("## 失败教训\n无", "## 失败教训\n第二次压缩"), [])]
        + [tc_output("done", "echo 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT' && echo finished", "cend")]
    )
    agent = make_agent(outputs, compression_threshold_tokens=5000, compression_keep_recent_turns=2)
    info = agent.run("long task")
    assert info["exit_status"] == "Submitted"
    # the first compression event message was itself folded into the second
    # compression (hierarchical compaction by design), so only event #2 survives
    events = compression_events(agent.messages)
    assert [e["index"] for e in events] == [2]
    assert events[0]["summary_source"] == "lm"
    assert agent._compression_count == 2
    assert_tool_calls_paired(agent.messages)


class _SummaryFormatErrorModel(DeterministicToolcallModel):
    """Raises FormatError on the summary call (single-message query), like real
    toolcall models do on text-only responses. Bills the call first (litellm adds
    to GLOBAL_MODEL_STATS before parsing), but does not persist the response."""

    def query(self, messages, **kwargs):
        if len(messages) == 1:
            GLOBAL_MODEL_STATS.add(self.config.cost_per_call)
            raise FormatError(
                {
                    "role": "user",
                    "content": "No tool calls found in the response.",
                    "extra": {"interrupt_type": "FormatError", "cost": self.config.cost_per_call},
                }
            )
        return super().query(messages, **kwargs)


class _SummaryFormatErrorWithResponseModel(_SummaryFormatErrorModel):
    """Same, but persists the raw response dump the way litellm models do — the summary
    text must be recovered from it instead of falling back to the mechanical summary."""

    def query(self, messages, **kwargs):
        if len(messages) == 1:
            GLOBAL_MODEL_STATS.add(self.config.cost_per_call)
            raise FormatError(
                {
                    "role": "user",
                    "content": "No tool calls found in the response.",
                    "extra": {
                        "interrupt_type": "FormatError",
                        "cost": self.config.cost_per_call,
                        "response": {"choices": [{"message": {"role": "assistant", "content": SUMMARY_TEXT}}]},
                    },
                }
            )
        return super().query(messages, **kwargs)


def _outputs_for_summary_failure_tests() -> list[dict]:
    return (
        [
            tc_output(f"step {i}", f"echo step{i}-{BIG}", f"c{i}", usage_tokens=9000 if i == 2 else None)
            for i in range(3)
        ]
        + [tc_output(f"post {i}", f"echo post{i}-{BIG}", f"p{i}") for i in range(2)]
        + [tc_output("done", "echo 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT' && echo finished", "cend")]
    )


def test_format_error_without_persisted_response_falls_back_to_mechanical(reset_global_stats):
    agent = make_agent(
        model=_SummaryFormatErrorModel(outputs=_outputs_for_summary_failure_tests(), cost_per_call=0.5),
        compression_threshold_tokens=5000,
        compression_keep_recent_turns=2,
    )
    info = agent.run("fallback task")
    assert info["exit_status"] == "Submitted"  # summary path must never kill the run
    events = compression_events(agent.messages)
    assert len(events) == 1 and events[0]["summary_source"] == "mechanical"
    content = agent.messages[2]["content"]
    for header in SUMMARY_SECTION_HEADERS:
        assert header in content
    # FormatError cost (0.5, from the error message extra) still billed on top of the
    # 6 step calls. (No GLOBAL_MODEL_STATS invariant here: the fake toolcall output
    # hardcodes extra.cost=1.0 while this model's cost_per_call=0.5, so the two
    # accounting lines diverge by construction; the invariant is covered above.)
    assert agent.cost == pytest.approx(6.5)


def test_summary_text_recovered_from_persisted_format_error_response(reset_global_stats):
    agent = make_agent(
        model=_SummaryFormatErrorWithResponseModel(outputs=_outputs_for_summary_failure_tests(), cost_per_call=0.5),
        compression_threshold_tokens=5000,
        compression_keep_recent_turns=2,
    )
    info = agent.run("recovery task")
    assert info["exit_status"] == "Submitted"
    events = compression_events(agent.messages)
    assert len(events) == 1 and events[0]["summary_source"] == "lm"
    assert "## 任务目标" in agent.messages[2]["content"]
    assert agent.cost == pytest.approx(6.5)  # 6 step calls (extra.cost=1.0) + 0.5 recovered summary call


def test_shrink_guard_skips_rewrite_when_summary_would_grow_history():
    # Tiny folded region (short observations) + verbose LM summary: rewriting would
    # grow the history, so the guard must skip — and not re-trigger afterwards.
    outputs = (
        [tc_output(f"step {i}", f"echo step{i}", f"c{i}", usage_tokens=9000 if i == 2 else None) for i in range(3)]
        + [make_output(SUMMARY_TEXT, [])]
        + [tc_output(f"post {i}", f"echo post{i}", f"p{i}") for i in range(2)]
        + [tc_output("done", "echo 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT' && echo finished", "cend")]
    )
    agent = make_agent(outputs, compression_threshold_tokens=5000, compression_keep_recent_turns=2)
    info = agent.run("tiny task")
    assert info["exit_status"] == "Submitted"
    assert compression_events(agent.messages) == []  # rewrite skipped, no event recorded
    assert agent._compression_count == 0
    assert agent.messages[2]["role"] == "assistant"  # original history untouched


def test_pre_compression_snapshot_written(tmp_path: Path):
    traj = tmp_path / "traj.json"
    outputs = (
        [
            tc_output(f"step {i}", f"echo step{i}-{BIG}", f"c{i}", usage_tokens=9000 if i == 2 else None)
            for i in range(3)
        ]
        + [make_output(SUMMARY_TEXT, [])]
        + [tc_output("done", "echo 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT' && echo finished", "cend")]
    )
    agent = make_agent(
        outputs,
        compression_threshold_tokens=5000,
        compression_keep_recent_turns=2,
        output_path=traj,
    )
    info = agent.run("snapshot task")
    assert info["exit_status"] == "Submitted"

    snapshot_path = Path(f"{traj}.pre-compression-1.json")
    assert snapshot_path.exists()
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["snapshot_format"] == "mini-swe-agent-compression-snapshot-1.0"
    # pre-compression history: system, user + 3 full turns (assistant + tool each)
    assert len(snapshot["messages"]) == 8
    assert [m["role"] for m in snapshot["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]
    # the folded turn-0 content survives only in the snapshot, not the rewritten trajectory
    assert any("step 0" in json.dumps(m.get("content", "")) for m in snapshot["messages"][2:])

    final = json.loads(traj.read_text(encoding="utf-8"))
    assert final["trajectory_format"] == "mini-swe-agent-1.1"
    assert "step 0" not in json.dumps(final["messages"])  # folded away in the live trajectory
