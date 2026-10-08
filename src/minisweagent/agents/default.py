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
from minisweagent.agents.session_store import SessionStore
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
    session_db_path: str = ""
    """魔改 4: path to a SQLite file for session persistence (per-step event log +
    resume support). Empty string (default) disables persistence entirely — upstream
    behavior, byte-for-byte, and no DB file is created."""


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
        # 魔改 4: session persistence (off unless session_db_path is configured).
        self._session: SessionStore | None = None
        self.session_id: str = ""
        if getattr(self.config, "session_db_path", ""):
            self._session = SessionStore(self.config.session_db_path)

    def _agent_type(self) -> str:
        return f"{self.__class__.__module__}.{self.__class__.__name__}"

    def _session_state(self) -> dict:
        """Snapshot of the persistable state (the resume boundary, 魔改 4)."""
        return {
            "cost": self.cost,
            "n_calls": self.n_calls,
            "n_consecutive_format_errors": self.n_consecutive_format_errors,
            "compression_count": self._compression_count,
            "last_trigger_pos": self._messages_identity_pos(self._last_trigger_msg),
            "extra_template_vars": self.extra_template_vars,
            "cost_last_confirmed": float(getattr(self, "cost_last_confirmed", 0.0)),
            "start_time_epoch": self._start_time,
            "config_snapshot": self.config.model_dump(mode="json"),
        }

    def _messages_identity_pos(self, msg: dict | None) -> int | None:
        """Position of ``msg`` in the current message list by object identity.

        Mirrors the ``is``-based comparison used for the compression trigger
        marker (default.py:209): persistence uses the same identity semantics,
        expressed as a view position that survives a round-trip through the DB.
        """
        if msg is None:
            return None
        for i, m in enumerate(self.messages):
            if m is msg:
                return i
        return None

    def _persist(self, fn, *args, **kwargs) -> None:
        """Run a SessionStore write, degrading to log-and-continue on failure.

        Persistence must never kill a run: a failed write is rolled back inside
        the store and the next write re-syncs the state cache from memory.
        """
        if self._session is None:
            return
        try:
            fn(*args, **kwargs)
        except Exception:
            self.logger.error("Session persistence write failed; continuing.", exc_info=True)

    def _trajectory_extras(self) -> tuple[dict, ...]:
        """Extra dicts merged into the trajectory JSON (session id inside info)."""
        if self._session is None or not self.session_id:
            return ()
        return ({"info": {"session_id": self.session_id}},)

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
        start_pos = len(self.messages)
        self.messages.extend(messages)
        if self._session is not None and messages:
            # 魔改 4: append-only event rows + state cache refresh, one transaction.
            self._persist(self._session.append_messages, list(messages), start_pos=start_pos,
                          agent_state=self._session_state())
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
        if self._session is not None:
            self._persist(self._session.create_session, agent_state=self._session_state(),
                          agent_type=self._agent_type(), agent_version=__version__)
            self.session_id = self._session.session_id
        self.add_messages(
            self.model.format_message(role="system", content=self._render_template(self.config.system_template)),
            self.model.format_message(role="user", content=self._render_template(self.config.instance_template)),
        )
        return self._run_loop()

    def _run_loop(self) -> dict:
        """The step loop shared by run() (fresh start) and resume() (魔改 4 restore)."""
        while True:
            try:
                self.step()
                self.n_consecutive_format_errors = 0  # reset on any clean step
                if self._session is not None:
                    self._persist(self._session.update_state, agent_state=self._session_state())
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
                self.save(self.config.output_path, *self._trajectory_extras())
            if self.messages[-1].get("role") == "exit":
                if self._session is not None:
                    last_extra = self.messages[-1].get("extra", {})
                    self._persist(
                        self._session.finish,
                        agent_state=self._session_state()
                        | {
                            "exit_status": last_extra.get("exit_status", ""),
                            "submission": last_extra.get("submission", ""),
                        },
                    )
                break
        return self.messages[-1].get("extra", {})

    def resume(self, session_id: str) -> dict:
        """魔改 4: restore a persisted session and continue the agent loop.

        Restores the full decision state (messages, cost, step counters, compression
        bookkeeping, trigger marker, template vars). Wall-clock time restarts: the
        ``wall_time_limit`` budget applies to this continued run, not to the
        interrupted one. If the process died between an assistant response and its
        tool observation, the recorded actions are re-executed locally (at-least-once
        for tools); a recorded LM response is never re-queried, hence never re-billed.
        """
        if self._session is None:
            raise ValueError("resume() requires agent.session_db_path to be configured")
        state = self._session.attach(session_id)
        if state["status"] == "finished":
            raise ValueError(
                f"Session {session_id} is already finished (exit_status={state['exit_status']!r});"
                " nothing to resume."
            )
        self.session_id = session_id
        self.messages = self._session.load_messages()
        self.cost = float(state["cost"])
        self.n_calls = int(state["n_calls"])
        self.n_consecutive_format_errors = int(state["n_consecutive_format_errors"])
        self._compression_count = int(state["compression_count"])
        self._start_time = time.time()  # wall-clock budget restarts on resume
        self.extra_template_vars = dict(state["extra_template_vars"])
        if hasattr(self, "cost_last_confirmed"):
            self.cost_last_confirmed = float(state["cost_last_confirmed"])
        pos = state["last_trigger_pos"]
        self._last_trigger_msg = (
            self.messages[pos] if pos is not None and 0 <= pos < len(self.messages) else None
        )
        self.logger.info(
            "Resumed session %s: %s messages, %s steps, $%.4f spent so far.",
            session_id, len(self.messages), self.n_calls, self.cost,
        )
        if self.messages and self.messages[-1].get("role") == "exit":
            # Killed between the exit message and the finish() write: the run is over.
            extra = self.messages[-1].get("extra", {})
            self._persist(
                self._session.finish,
                agent_state=self._session_state()
                | {"exit_status": extra.get("exit_status", ""), "submission": extra.get("submission", "")},
            )
            return extra
        if self.messages and self.messages[-1].get("role") == "assistant":
            actions = (self.messages[-1].get("extra") or {}).get("actions") or []
            if actions:
                self.logger.info("Re-executing %s dangling action(s) from the interrupted step.", len(actions))
                self.execute_actions(self.messages[-1])
        self._persist(self._session.update_state, agent_state=self._session_state())
        return self._run_loop()

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
            error_extra = e.messages[0].get("extra") or {}
            summary_cost = error_extra.get("cost", 0.0)
            self.cost += summary_cost
            text = content_from_persisted_response(error_extra.get("response"))
            if text is None:
                self.logger.warning(
                    "Summary call hit FormatError without a recoverable response; using mechanical summary."
                )
                return None, summary_cost
        except Exception:
            self.logger.warning("Summary call failed; using mechanical summary.", exc_info=True)
            return None, 0.0
        else:
            summary_cost = (message.get("extra") or {}).get("cost", 0.0)
            self.cost += summary_cost
            text = content_text(message.get("content"))
        if not validate_summary(text):
            self.logger.warning("LM summary failed five-section validation; using mechanical summary.")
            return None, summary_cost
        return text, summary_cost

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
            if self._session is not None:
                # 魔改 4: mirror the rewrite in the DB *before* touching memory — the
                # store supersedes folded rows, shifts kept rows and inserts the
                # event message in one transaction, so a crash mid-rewrite leaves
                # a consistent (post-rewrite) state behind.
                self._persist(
                    self._session.apply_compression,
                    cut_index=cut_index,
                    event_message=event_message,
                    agent_state=self._session_state(),
                )
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
        if self._session is not None:
            # 魔改 4: mark the execution as in-flight *before* it runs, so a crash
            # between "tool began" and "observation persisted" is explicit in the
            # event log (at-least-once tool semantics on resume).
            self._persist(self._session.record_action_started, action=action)
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
