"""Context compression helpers for the default agent (魔改 3).

Upstream mini-swe-agent resends the full message history on every model call and never
compresses it; the only safeguard against a blown context window is the model layer's
``ContextWindowExceededError`` abort (which 魔改 2 already turns into a controlled exit).
This module implements the *preventive* side: fold early turns into a structured summary
while keeping the most recent N turns verbatim.

Design constraints that shape every helper here:

- **Turn boundaries are the only safe cut points.** Under the OpenAI protocol an
  ``assistant`` message carrying ``tool_calls`` must be immediately followed by the
  matching ``tool`` result messages. We therefore define a *turn* as one assistant
  message plus everything after it up to the next assistant message, and only ever cut
  at turn starts.
- **messages[0] (system) and messages[1] (instance task) are never folded.**
- **Token counting prefers the real signal.** Every step resends the full history, so
  the previous response's ``usage.prompt_tokens`` (persisted in the assistant message's
  ``extra.response`` by litellm-backed models) is the most accurate measure of the
  current history size. When it is unavailable (e.g. deterministic test models), a
  character estimate is used instead.

All functions in this module are pure; the actual history rewrite lives in
:class:`~minisweagent.agents.default.DefaultAgent`.
"""

import json
import re

# Fixed five-section summary layout. Both the LM-generated summary and the mechanical
# fallback produce exactly these headers, and the LM output is validated against them.
SUMMARY_SECTION_HEADERS: list[str] = [
    "## 任务目标",
    "## 已完成步骤",
    "## 关键文件与产物路径",
    "## 当前计划与下一步",
    "## 失败教训",
]

DEFAULT_SUMMARY_PROMPT = """你是对话历史压缩器。下面提供一段智能体执行历史的折叠投影。\
请把它压缩成一份结构化摘要，供智能体在原始消息被丢弃后继续任务时使用。

严格要求：
1. 输出必须且只能包含以下五个小节，顺序一致，每节以二级标题开头：
## 任务目标
## 已完成步骤
## 关键文件与产物路径
## 当前计划与下一步
## 失败教训
2. 「关键文件与产物路径」必须逐条列出消息中出现过的文件路径（绝对路径优先）；\
已写入文件的重要中间产物必须保留路径。
3. 「失败教训」逐条保留失败原因与规避方式；无失败则写「无」。
4. 不要输出五个小节之外的任何内容。
5. 记住：重要产物应继续用 bash 重定向写入文件持久保存——对话历史可能再次被压缩，文件不会。
"""

COMPRESSION_PREAMBLE = (
    "[历史压缩] 对话前段已被折叠为以下结构化摘要（原始消息不再保留）。"
    "关键状态以此为准；已写入文件的产物以文件内容为准。\n\n"
)

# Path-like tokens: drive letters, absolute POSIX paths, and dotfiles with extensions.
# Used by the mechanical fallback to salvage artifact paths from folded observations.
_PATH_PATTERN = re.compile(r"(?:[A-Za-z]:)?(?:/|\\)[^\s\"'`<>|*?]{2,}")


def content_text(content) -> str:
    """Flatten a message content field (str or multimodal list) to plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return str(content)


def _message_text(msg: dict) -> str:
    """Approximate the text a message contributes to the prompt (content + tool calls)."""
    parts = [content_text(msg.get("content"))]
    tool_calls = msg.get("tool_calls")
    if tool_calls:
        try:
            parts.append(json.dumps(tool_calls, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            parts.append(str(tool_calls))
    return "\n".join(parts)


def truncate(text: str, limit: int) -> str:
    """Truncate text to ``limit`` characters, marking the cut."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...(truncated, {len(text) - limit} more chars)"


def estimate_tokens(messages: list[dict], chars_per_token: float = 4.0) -> int:
    """Estimate the prompt-token size of a message list when no real usage is available."""
    if chars_per_token <= 0:
        chars_per_token = 4.0
    chars = sum(len(_message_text(msg)) for msg in messages)
    return max(1, int(chars / chars_per_token))


def last_prompt_usage(messages: list[dict]) -> tuple[int, int] | None:
    """``(index, prompt_tokens)`` of the most recent assistant turn carrying usage, or None.

    litellm-backed models dump the whole API response (including ``usage``) into
    ``extra.response``. Because the full history is resent on every call, the last
    response's prompt_tokens equals the size of the history sent on the *next* call
    (plus tool definitions, which are constant). The index lets callers mark the
    signal as consumed — a compression rewrite keeps the kept turns verbatim, so
    their stale usage would otherwise re-trigger compression forever.
    """
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") != "assistant":
            continue
        extra = msg.get("extra") or {}
        response = extra.get("response")
        if not isinstance(response, dict):
            continue
        usage = response.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens")
        if isinstance(prompt_tokens, (int, float)) and prompt_tokens > 0:
            return (i, int(prompt_tokens))
    return None


def assistant_turn_starts(messages: list[dict], first_index: int = 2) -> list[int]:
    """Indices where a new turn begins (each assistant message at/after ``first_index``)."""
    return [i for i, msg in enumerate(messages) if i >= first_index and msg.get("role") == "assistant"]


def find_cut_index(messages: list[dict], keep_recent_turns: int = 5, first_index: int = 2) -> int | None:
    """Index at which foldable history ends, or None if there is nothing to fold.

    The cut index is the start of the first *kept* turn, so ``messages[:cut_index]``
    (minus the never-folded system and instance messages) is exactly a whole number of
    turns plus any leading orphans — tool_calls/tool-result pairing is preserved by
    construction. Requires at least one fully foldable turn beyond the kept tail.
    """
    if keep_recent_turns < 0:
        keep_recent_turns = 0
    starts = assistant_turn_starts(messages, first_index=first_index)
    if len(starts) <= keep_recent_turns:
        return None
    return starts[len(starts) - keep_recent_turns]


def build_folded_transcript(
    messages: list[dict], cut_index: int, *, first_index: int = 2, max_msg_chars: int = 1200
) -> str:
    """Project ``messages[first_index:cut_index]`` into a plain-text transcript for the summarizer.

    This is deliberately a *text projection*, never raw messages: the summary call goes
    through the model's normal query path, and feeding it raw assistant/tool messages
    without their pairing context would violate the tool_calls protocol.
    """
    lines: list[str] = []
    for msg in messages[first_index:cut_index]:
        role = msg.get("role", "?")
        text = truncate(content_text(msg.get("content")), max_msg_chars)
        if role == "assistant":
            tool_calls = msg.get("tool_calls") or []
            calls = "; ".join(
                f"{tc.get('function', {}).get('name', '?')}({tc.get('function', {}).get('arguments', '')})"
                for tc in tool_calls
                if isinstance(tc, dict)
            )
            body = text if text else "(no text)"
            lines.append(f"[assistant] {body}" + (f" | tool_calls: {calls}" if calls else ""))
        else:
            lines.append(f"[{role}] {text}")
    return "\n".join(lines)


def validate_summary(text: str) -> bool:
    """A usable LM summary must contain all five fixed sections, in order."""
    pos = -1
    for header in SUMMARY_SECTION_HEADERS:
        idx = text.find(header)
        if idx < 0 or idx < pos:
            return False
        pos = idx
    return True


def content_from_persisted_response(response) -> str | None:
    """Recover assistant text from a litellm response dump persisted on a FormatError.

    Toolcall-backed models raise ``FormatError`` on any text-only response (``if not
    tool_calls`` in ``parse_toolcall_actions``), and the error message carries only the
    rendered error template — but the model layer persists the raw response dump on
    ``e.messages[0]["extra"]["response"]`` exactly so "response MUST be persisted"
    holds. The summary text lives there and can be salvaged from it.
    """
    if not isinstance(response, dict):
        return None
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    text = content_text(content)
    return text or None


def _failure_events(messages: list[dict]) -> list[str]:
    """One line per failed observation in the folded region (uses 魔改 2's error_class)."""
    events: list[str] = []
    for i, msg in enumerate(messages):
        extra = msg.get("extra") or {}
        error_class = extra.get("error_class")
        returncode = extra.get("returncode")
        exception_info = extra.get("exception_info")
        if not error_class and returncode in (None, 0) and not exception_info:
            continue
        parts = [f"msg#{i}"]
        if error_class:
            parts.append(f"error_class={error_class}")
        if returncode not in (None, 0):
            parts.append(f"returncode={returncode}")
        if exception_info:
            parts.append(f"exception={truncate(str(exception_info), 200)}")
        events.append("- " + ", ".join(parts))
    return events


def mechanical_summary(
    messages: list[dict], cut_index: int, *, first_index: int = 2, task: str = "", max_paths: int = 30
) -> str:
    """Programmatic five-section fallback summary (no LM call, lossy but safe).

    Used when the LM summary call fails or its output fails validation, so a broken
    summary path can never kill the run. Salvages what is mechanically extractable:
    step list from turn structure, artifact paths via regex, failure events from the
    ``error_class``/``returncode`` channels 魔改 2 added to observation extras.
    """
    folded = messages[first_index:cut_index]
    turn_starts = [i for i, msg in enumerate(folded) if msg.get("role") == "assistant"]

    # Section 1: task goal.
    goal = (
        task.strip() if task else truncate(content_text(messages[1].get("content")) if len(messages) > 1 else "", 600)
    )
    lines = [SUMMARY_SECTION_HEADERS[0], truncate(goal, 600), ""]

    # Section 2: completed steps, one per folded turn.
    lines += [SUMMARY_SECTION_HEADERS[1]]
    if not turn_starts:
        lines.append("-（无可折叠的执行步骤）")
    for idx, start in enumerate(turn_starts):
        end = turn_starts[idx + 1] if idx + 1 < len(turn_starts) else len(folded)
        n_observations = max(0, end - start - 1)
        text = truncate(content_text(folded[start].get("content")), 160).replace("\n", " ")
        tool_calls = folded[start].get("tool_calls") or []
        calls = ", ".join(f"{tc.get('function', {}).get('name', '?')}" for tc in tool_calls if isinstance(tc, dict))
        desc = text or (f"tool_calls: {calls}" if calls else "(no text)")
        lines.append(f"- 步骤 {idx + 1}: {desc}（{n_observations} 条观察）")
    lines.append("")

    # Section 3: artifact paths mentioned anywhere in the folded region.
    paths: list[str] = []
    for msg in folded:
        for match in _PATH_PATTERN.findall(content_text(msg.get("content"))):
            if match not in paths:
                paths.append(match)
    lines += [SUMMARY_SECTION_HEADERS[2]]
    lines += [f"- {p}" for p in paths[:max_paths]] or ["-（未发现文件路径）"]
    if len(paths) > max_paths:
        lines.append(f"-（另有 {len(paths) - max_paths} 个路径未列出）")
    lines.append("")

    # Section 4: current plan — best mechanical proxy is the last assistant text.
    last_text = ""
    if turn_starts:
        last_text = content_text(folded[turn_starts[-1]].get("content"))
    lines += [SUMMARY_SECTION_HEADERS[3], truncate(last_text.strip(), 400) or "（无）", ""]

    # Section 5: failure lessons from the structured error channels.
    events = _failure_events(folded)
    lines += [SUMMARY_SECTION_HEADERS[4], "\n".join(events) if events else "无"]
    return "\n".join(lines)
