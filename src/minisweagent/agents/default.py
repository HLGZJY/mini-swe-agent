"""Basic agent class. See https://mini-swe-agent.com/latest/advanced/control_flow/ for visual explanation
or https://minimal-agent.com for a tutorial on the basic building principles.
"""

import json
import logging
import time
import traceback
from pathlib import Path
from typing import Literal

from jinja2 import StrictUndefined, Template
from pydantic import BaseModel

from minisweagent import Environment, Model, __version__
from minisweagent.agents.compression import (
    COMPRESSION_PREAMBLE,
    DEFAULT_SUMMARY_PROMPT,
    build_folded_transcript,
    content_from_persisted_response,
    content_text,
    estimate_tokens,
    find_cut_index,
    last_prompt_usage,
    mechanical_summary,
    validate_summary,
)
from minisweagent.exceptions import FormatError, InterruptAgentFlow, LimitsExceeded, TimeExceeded
from minisweagent.models.utils.tool_registry import get_default_registry
from minisweagent.utils.serialize import recursive_merge


class AgentConfig(BaseModel):
    """Check the config files in minisweagent/config for example settings."""

    system_template: str
    """Template for the system message (the first message)."""
    instance_template: str
    """Template for the first user message specifying the task (the second message overall)."""
    step_limit: int = 0
    """Maximum number of steps the agent can take."""
    cost_limit: float = 3.0
    """Stop agent after exceeding (!) this cost."""
    wall_time_limit_seconds: int = 0
    """Stop agent after this many seconds of wall-clock time. 0 means no limit."""
    max_consecutive_format_errors: int = 3
    """Exit after this many format errors in a row (0 = no limit)."""
    on_uncaught_exception: Literal["raise", "controlled_exit"] = "raise"
    """What to do with exceptions that escape the step loop (e.g. model-layer retry
    budget exhausted, environment died). ``raise`` = upstream behavior: re-raise after
    recording the exit message (process dies with a traceback). ``controlled_exit`` =
    log the traceback, keep the recorded exit message and stop the loop — a clean
    exit_status in the trajectory instead of a crash. Off by default: the bare raise
    is also the only channel that surfaces bugs in our own code."""
    compression_threshold_tokens: int = 0
    """魔改 3: fold early turns into a structured summary when the history reaches this many
    tokens. 0 (default) disables compression entirely — upstream behavior, byte-for-byte.
    Suggested value: model context window / 1.5, leaving headroom for the summary and
    the kept recent turns."""
    compression_keep_recent_turns: int = 5
    """魔改 3: number of most-recent turns (assistant + its observations) kept verbatim
    when compression fires."""
    compression_chars_per_token: float = 4.0
    """魔改 3: character-to-token ratio for the fallback estimate used when the model
    response carries no ``usage`` (e.g. deterministic test models)."""
    compression_summary_prompt: str = ""
    """魔改 3: instruction for the LM-generated summary. Empty string uses the built-in
    default (five fixed sections, validated after generation)."""
    output_path: Path | None = None
    """Save the trajectory to this path."""


class DefaultAgent:
    def __init__(self, model: Model, env: Environment, *, config_class: type = AgentConfig, **kwargs):
        """See the `AgentConfig` class for permitted keyword arguments."""
        self.config = config_class(**kwargs)
        self.messages: list[dict] = []
        self.model = model
        self.env = env
        self.extra_template_vars = {}
        self.logger = logging.getLogger("agent")
        self.cost = 0.0
        self.n_calls = 0
        self.n_consecutive_format_errors = 0
        self._start_time = time.time()
        self._compression_count = 0
        self._last_trigger_msg: dict | None = None

    def get_template_vars(self, **kwargs) -> dict:
        return recursive_merge(
            self.config.model_dump(),
            self.env.get_template_vars(),
            self.model.get_template_vars(),
            {
                "n_model_calls": self.n_calls,
                "model_cost": self.cost,
                "elapsed_seconds": int(time.time() - self._start_time),
            },
            self.extra_template_vars,
            kwargs,
        )

    def _render_template(self, template: str) -> str:
        return Template(template, undefined=StrictUndefined).render(**self.get_template_vars())

    def add_messages(self, *messages: dict) -> list[dict]:
        self.logger.debug(messages)  # set log level to debug to see
        self.messages.extend(messages)
        return list(messages)

    def handle_uncaught_exception(self, e: Exception) -> list[dict]:
        return self.add_messages(
            self.model.format_message(
                role="exit",
                content=str(e),
                extra={
                    "exit_status": type(e).__name__,
                    "submission": "",
                    "exception_str": str(e),
                    "traceback": traceback.format_exc(),
                },
            )
        )

    def run(self, task: str = "", **kwargs) -> dict:
        """Run step() until agent is finished. Returns dictionary with exit_status, submission keys."""
        self.extra_template_vars |= {"task": task, **kwargs}
        self.messages = []
        self._last_trigger_msg = None
        self.add_messages(
            self.model.format_message(role="system", content=self._render_template(self.config.system_template)),
            self.model.format_message(role="user", content=self._render_template(self.config.instance_template)),
        )
        while True:
            try:
                self.step()
                self.n_consecutive_format_errors = 0  # reset on any clean step
            except FormatError as e:
                # The call was billed before parsing failed, so query() never got to charge it.
                self.cost += e.messages[0].get("extra", {}).get("cost", 0.0)
                self.n_consecutive_format_errors += 1
                if 0 < self.config.max_consecutive_format_errors <= self.n_consecutive_format_errors:
                    self.add_messages(
                        *e.messages,
                        {
                            "role": "exit",
                            "content": "RepeatedFormatError",
                            "extra": {"exit_status": "RepeatedFormatError", "submission": ""},
                        },
                    )
                else:
                    self.add_messages(*e.messages)
            except InterruptAgentFlow as e:
                self.add_messages(*e.messages)
            except Exception as e:
                self.handle_uncaught_exception(e)
                if self.config.on_uncaught_exception != "controlled_exit":
                    raise
                self.logger.error("Uncaught exception, controlled exit:\n%s", traceback.format_exc())
            finally:
                self.save(self.config.output_path)
            if self.messages[-1].get("role") == "exit":
                break
        return self.messages[-1].get("extra", {})

    def step(self) -> list[dict]:
        """Query the LM, execute actions."""
        return self.execute_actions(self.query())

    def query(self) -> dict:
        """Query the model and return model messages. Override to add hooks."""
        if 0 < self.config.step_limit <= self.n_calls or 0 < self.config.cost_limit <= self.cost:
            raise LimitsExceeded(
                {
                    "role": "exit",
                    "content": "LimitsExceeded",
                    "extra": {"exit_status": "LimitsExceeded", "submission": ""},
                }
            )
        if 0 < self.config.wall_time_limit_seconds <= int(time.time() - self._start_time):
            raise TimeExceeded(
                {
                    "role": "exit",
                    "content": "TimeExceeded",
                    "extra": {"exit_status": "TimeExceeded", "submission": ""},
                }
            )
        self._maybe_compress()
        self.n_calls += 1
        message = self.model.query(self.messages)
        self.cost += message.get("extra", {}).get("cost", 0.0)
        self.add_messages(message)
        return message

    def _maybe_compress(self) -> None:
        """魔改 3: check whether the history should be folded before the next model call.

        Disabled entirely while ``compression_threshold_tokens <= 0``. The token signal
        is the previous response's real ``usage.prompt_tokens`` when available (each
        step resends the full history, so it measures the upcoming call exactly);
        otherwise a character estimate.
        """
        threshold = self.config.compression_threshold_tokens
        if threshold <= 0:
            return
        usage = last_prompt_usage(self.messages)
        if usage is not None:
            usage_index, tokens = usage
            if self.messages[usage_index] is self._last_trigger_msg:
                # This usage signal was already consumed by a previous compression
                # attempt (kept turns still carry it after a rewrite) — waiting for a
                # fresh assistant response prevents a re-trigger loop.
                return
            source = "usage"
        else:
            tokens = estimate_tokens(self.messages, self.config.compression_chars_per_token)
            usage_index = None
            source = "estimate"
        if tokens < threshold:
            return
        cut_index = find_cut_index(self.messages, self.config.compression_keep_recent_turns)
        if cut_index is None:
            self.logger.debug(
                "Compression triggered (~%s tokens) but fewer than %s foldable turns; skipping.",
                tokens,
                self.config.compression_keep_recent_turns + 1,
            )
            return
        self.logger.info(
            "Compression triggered (%s=%s tokens >= threshold %s); folding messages [2:%s).",
            source,
            tokens,
            threshold,
            cut_index,
        )
        trigger_msg = self.messages[usage_index] if usage_index is not None else None
        self._compress(cut_index, tokens_before=tokens, trigger_source=source, trigger_msg=trigger_msg)

    def _generate_summary(self, transcript: str) -> tuple[str | None, float]:
        """Ask the model for a structured summary of the folded transcript.

        Returns ``(summary_text, cost)``; ``summary_text`` is None when the LM path
        failed or produced an invalid summary — the caller then falls back to the
        mechanical summary, so a broken summary path can never kill the run.

        Cost is always accounted into ``self.cost`` (the summary call bypasses the
        agent loop's own accounting, which only sees step queries).
        """
        instruction = self.config.compression_summary_prompt or DEFAULT_SUMMARY_PROMPT
        content = f"{instruction}\n\n<folded_history>\n{transcript}\n</folded_history>"
        try:
            message = self.model.query([{"role": "user", "content": content}])
        except FormatError as e:
            # Toolcall-backed models raise FormatError on any text-only response, and
            # the error message carries only the rendered error template. The raw
            # response dump (with the actual summary text) is persisted on the error's
            # message extra — salvage it from there.
            self.cost += (e.messages[0].get("extra") or {}).get("cost", 0.0)
            response = (e.messages[0].get("extra") or {}).get("response")
            text = content_from_persisted_response(response)
            if text is None:
                self.logger.warning(
                    "Summary call hit FormatError without a recoverable response; using mechanical summary."
                )
                return None, 0.0
        except Exception:
            self.logger.warning("Summary call failed; using mechanical summary.", exc_info=True)
            return None, 0.0
        cost = (message.get("extra") or {}).get("cost", 0.0)
        self.cost += cost
        text = content_text(message.get("content"))
        if not validate_summary(text):
            self.logger.warning("LM summary failed five-section validation; using mechanical summary.")
            return None, cost
        return text, cost

    def _compress(self, cut_index: int, *, tokens_before: int, trigger_source: str, trigger_msg: dict | None) -> None:
        """Fold ``messages[2:cut_index]`` into a summary message and rewrite the history in place.

        In-place rewriting keeps the upstream invariant that ``self.messages`` is the
        single source of truth (exit checks, serialization, template vars all read it).
        Trajectory completeness is preserved by two mechanisms: a compression event
        message records what was folded, and a snapshot of the pre-compression history
        is written next to the trajectory file before the rewrite.
        """
        self._compression_count += 1
        transcript = build_folded_transcript(self.messages, cut_index)
        summary, summary_cost = self._generate_summary(transcript)
        if summary is None:
            summary = mechanical_summary(self.messages, cut_index, task=str(self.extra_template_vars.get("task", "")))
            summary_source = "mechanical"
        else:
            summary_source = "lm"

        event_message = {
            "role": "user",
            "content": COMPRESSION_PREAMBLE + summary,
            "extra": {
                "compression_event": {
                    "index": self._compression_count,
                    "trigger": {"source": trigger_source, "tokens_before": tokens_before},
                    "folded_message_count": cut_index - 2,
                    "kept_messages": len(self.messages) - cut_index,
                    "summary_source": summary_source,
                    "summary_cost": summary_cost,
                },
                "timestamp": time.time(),
            },
        }
        new_messages = self.messages[:2] + [event_message] + self.messages[cut_index:]

        # Safety net: never let a compression *grow* the history (pathological cases,
        # e.g. a mechanical summary longer than what it replaces). Compared on the same
        # estimation basis so mixed real-usage/estimate scales don't produce a false alarm.
        est_before = estimate_tokens(self.messages, self.config.compression_chars_per_token)
        est_after = estimate_tokens(new_messages, self.config.compression_chars_per_token)
        if est_after >= est_before:
            self._compression_count -= 1
            self.logger.warning(
                "Compression would not shrink history (%s -> %s estimated tokens); skipping rewrite.",
                est_before,
                est_after,
            )
        else:
            new_messages[2]["extra"]["compression_event"]["tokens_after_estimate"] = est_after
            self._write_compression_snapshot()
            self.messages[:] = new_messages
            self.logger.info(
                "Compressed %s messages into a %s summary (event #%s).",
                cut_index - 2,
                summary_source,
                self._compression_count,
            )
        # Either way the trigger signal is spent: the summary call was already paid for,
        # and retrying it every step against an unwritable history would just burn tokens.
        self._last_trigger_msg = trigger_msg

    def _write_compression_snapshot(self) -> None:
        """Preserve the full pre-compression history next to the trajectory file.

        The trajectory is rewritten in place (messages-as-state) and ``save()``
        overwrites ``output_path`` on every step, so without this snapshot the
        original history would exist nowhere. Ablations read the real per-step
        ``prompt_tokens`` from these snapshot files. A snapshot failure is logged
        and swallowed: it must never kill the run.
        """
        if self.config.output_path is None:
            return
        path = Path(f"{self.config.output_path}.pre-compression-{self._compression_count}.json")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "snapshot_format": "mini-swe-agent-compression-snapshot-1.0",
                        "compression_index": self._compression_count,
                        "snapshot_time": time.time(),
                        "messages": self.messages,
                    },
                    indent=2,
                    default=str,
                )
            )
            self.logger.info("Pre-compression snapshot written to %s", path)
        except OSError:
            self.logger.warning("Failed to write pre-compression snapshot to %s; continuing.", path, exc_info=True)

    def _execute_action(self, action: dict) -> dict:
        """Execute a single action: in-process structured tool, or environment command.

        Structured tool actions (``{"tool": name, "args": {...}, "tool_call_id": ...}``)
        are validated and executed by the tool registry; failures (schema validation,
        tool exceptions) come back as observations so the model can self-correct.
        All other actions go to the environment as before.
        """
        if "tool" in action:
            # Structured tools run in-process, NOT in a shell: they must resolve relative
            # paths against the environment's working directory (if it exposes one).
            return get_default_registry().execute_action(action, base_dir=getattr(self.env, "cwd", None))
        return self.env.execute(action)

    def execute_actions(self, message: dict) -> list[dict]:
        """Execute actions in message, add observation messages, return them."""
        outputs = [self._execute_action(action) for action in message.get("extra", {}).get("actions", [])]
        return self.add_messages(*self.model.format_observation_messages(message, outputs, self.get_template_vars()))

    def serialize(self, *extra_dicts) -> dict:
        """Serialize agent state to a json-compatible nested dictionary for saving."""
        last_message = self.messages[-1] if self.messages else {}
        last_extra = last_message.get("extra", {})
        agent_data = {
            "info": {
                "model_stats": {
                    "instance_cost": self.cost,
                    "api_calls": self.n_calls,
                },
                "config": {
                    "agent": self.config.model_dump(mode="json"),
                    "agent_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                },
                "mini_version": __version__,
                "exit_status": last_extra.get("exit_status", ""),
                "submission": last_extra.get("submission", ""),
            },
            "messages": self.messages,
            "trajectory_format": "mini-swe-agent-1.1",
        }
        return recursive_merge(agent_data, self.model.serialize(), self.env.serialize(), *extra_dicts)

    def save(self, path: Path | None, *extra_dicts) -> dict:
        """Save the trajectory of the agent to a file if path is given. Returns full serialized data.
        You can pass additional dictionaries with extra data to be (recursively) merged into the output data.
        """
        data = self.serialize(*extra_dicts)
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2))
        return data
