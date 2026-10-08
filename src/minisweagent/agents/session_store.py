"""魔改 4: SQLite-backed session persistence for agents.

Design (documented in 产出/魔改4-Session持久化.md):

- **Append-only event rows.** Every ``add_messages`` batch inserts one row per
  message with the *full* message dict as JSON (``data`` column) — content may be
  a string or a multimodal list and toolcall messages carry top-level keys beyond
  ``role``/``content``/``extra``, so only the full dict round-trips losslessly.
  Rows are never updated or deleted.
- **``view_pos`` = position in the current effective view.** Compression rewrites
  (魔改 3) supersede folded rows (``view_pos = NULL``) and insert the summary
  event at the folded position, all inside one transaction — the DB and the
  in-memory history are always either both pre- or both post-rewrite, so a crash
  mid-compression leaves a consistent state behind. Superseded rows stay in the
  table: the DB is *also* the full event stream, and "what did the model see at
  step K" remains answerable after any number of compressions.
- **``action_started`` rows** mark tool executions in flight, making a crash
  between "tool began" and "observation persisted" explicit. Resume semantics:
  at-least-once for tool execution, never for LM calls (a recorded response is
  restored verbatim, never re-billed).
- **The sessions row** caches cumulative agent state (cost, n_calls, ...) and is
  updated in the same transaction as the event inserts. On resume the message
  rows are the source of truth and the cached counters are taken as-is (they are
  committed atomically with the events they describe).
- **WAL journal + ``synchronous=FULL``**: a SIGKILL of the process never loses a
  committed transaction.

Zero new dependencies: stdlib ``sqlite3`` only. An agent with
``session_db_path=""`` (default) never touches this module.
"""

import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("agent.session")

_SCHEMA_VERSION = 2

_SCHEMA = f"""
PRAGMA user_version = {_SCHEMA_VERSION};

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    exit_status TEXT NOT NULL DEFAULT '',
    submission TEXT NOT NULL DEFAULT '',
    task TEXT NOT NULL DEFAULT '',
    cost REAL NOT NULL DEFAULT 0.0,
    n_calls INTEGER NOT NULL DEFAULT 0,
    n_consecutive_format_errors INTEGER NOT NULL DEFAULT 0,
    start_time_epoch REAL NOT NULL DEFAULT 0.0,
    compression_count INTEGER NOT NULL DEFAULT 0,
    last_trigger_pos INTEGER,
    extra_template_vars TEXT NOT NULL DEFAULT '{{}}',
    cost_last_confirmed REAL NOT NULL DEFAULT 0.0,
    agent_type TEXT NOT NULL DEFAULT '',
    agent_version TEXT NOT NULL DEFAULT '',
    config_snapshot TEXT NOT NULL DEFAULT '{{}}',
    stall_streak INTEGER NOT NULL DEFAULT 0,
    stall_last_fp TEXT,
    stall_warnings INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    view_pos INTEGER,
    kind TEXT NOT NULL DEFAULT 'message',
    role TEXT NOT NULL DEFAULT '',
    cost REAL NOT NULL DEFAULT 0.0,
    data TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_messages_view
    ON messages (session_id, view_pos) WHERE view_pos IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_messages_kind
    ON messages (session_id, kind);

CREATE VIEW IF NOT EXISTS v_steps AS
SELECT m1.session_id AS session_id,
       m1.view_pos AS step,
       json_extract(m1.data, '$.content') AS thought,
       m1.cost AS cost,
       json_extract(m1.data, '$.extra.actions[0].command') AS tool_command,
       json_extract(m1.data, '$.extra.actions[0].tool') AS tool_name,
       json_extract(m2.data, '$.content') AS observation,
       m1.created_at AS created_at
  FROM messages m1
  LEFT JOIN messages m2
    ON m2.session_id = m1.session_id
   AND m2.view_pos = m1.view_pos + 1
   AND m2.kind = 'message'
 WHERE m1.kind = 'message'
   AND m1.view_pos IS NOT NULL
   AND m1.role = 'assistant';
"""


def _msg_cost(msg: dict) -> float:
    extra = msg.get("extra") or {}
    cost = extra.get("cost", 0.0)
    try:
        return float(cost or 0.0)
    except (TypeError, ValueError):
        return 0.0


class SessionStore:
    """SQLite façade for one agent's session persistence. Not thread-safe by design:
    an agent loop is single-threaded."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(_SCHEMA)
        # 魔改 5: migrate v1 DBs — add stall columns if missing (idempotent via
        # table_info, so a crash mid-migration self-heals on the next open).
        existing = {r[1] for r in self._conn.execute("PRAGMA table_info(sessions)")}
        for col, decl in (
            ("stall_streak", "INTEGER NOT NULL DEFAULT 0"),
            ("stall_last_fp", "TEXT"),
            ("stall_warnings", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if col not in existing:
                self._conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} {decl}")
        self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        self._conn.commit()
        self.session_id: str = ""
        self._next_seq: int = 0

    def close(self) -> None:
        self._conn.close()

    # --- session lifecycle -------------------------------------------------

    def create_session(self, *, agent_state: dict, agent_type: str, agent_version: str) -> str:
        """Create a new session row and return its id."""
        session_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        now = time.time()
        self._conn.execute(
            "INSERT INTO sessions (session_id, created_at, updated_at, start_time_epoch,"
            " agent_type, agent_version, config_snapshot, extra_template_vars, task,"
            " cost, n_calls, n_consecutive_format_errors, compression_count, last_trigger_pos,"
            " cost_last_confirmed, stall_streak, stall_last_fp, stall_warnings)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                now,
                now,
                float(agent_state.get("start_time_epoch") or now),
                agent_type,
                agent_version,
                json.dumps(agent_state.get("config_snapshot", {}), ensure_ascii=False, default=str),
                json.dumps(agent_state.get("extra_template_vars", {}), ensure_ascii=False, default=str),
                str((agent_state.get("extra_template_vars") or {}).get("task", "")),
                float(agent_state.get("cost", 0.0)),
                int(agent_state.get("n_calls", 0)),
                int(agent_state.get("n_consecutive_format_errors", 0)),
                int(agent_state.get("compression_count", 0)),
                agent_state.get("last_trigger_pos"),
                float(agent_state.get("cost_last_confirmed", 0.0)),
                int(agent_state.get("stall_streak", 0) or 0),
                agent_state.get("stall_last_fp"),
                int(agent_state.get("stall_warnings", 0) or 0),
            ),
        )
        self._conn.commit()
        self.session_id = session_id
        self._next_seq = 0
        return session_id

    def attach(self, session_id: str) -> dict[str, Any]:
        """Attach to an existing session and return its state row for restore."""
        row = self._conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            self._conn.close()
            raise ValueError(f"Session {session_id!r} not found in {self.db_path}")
        self.session_id = session_id
        self._next_seq = self._conn.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 FROM messages WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
        return self._row_to_state(dict(row))

    @staticmethod
    def _row_to_state(row: dict) -> dict[str, Any]:
        row["extra_template_vars"] = json.loads(row.get("extra_template_vars") or "{}")
        row["config_snapshot"] = json.loads(row.get("config_snapshot") or "{}")
        return row

    # --- event writes ------------------------------------------------------

    def append_messages(self, messages: list[dict], *, start_pos: int, agent_state: dict) -> None:
        """Insert message event rows and refresh the state cache in one transaction."""
        now = time.time()
        rows = []
        for i, msg in enumerate(messages):
            rows.append(
                (
                    self.session_id,
                    self._next_seq,
                    start_pos + i,
                    str(msg.get("role", "")),
                    _msg_cost(msg),
                    json.dumps(msg, ensure_ascii=False, default=str),
                    now,
                )
            )
            self._next_seq += 1
        cur = self._conn.cursor()
        try:
            cur.executemany(
                "INSERT INTO messages (session_id, seq, view_pos, kind, role, cost, data, created_at)"
                " VALUES (?, ?, ?, 'message', ?, ?, ?, ?)",
                rows,
            )
            self._update_state_sql(cur, agent_state)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def record_action_started(self, action: dict) -> None:
        """Mark a tool execution as in-flight (crash forensics; never part of the view)."""
        now = time.time()
        self._conn.execute(
            "INSERT INTO messages (session_id, seq, view_pos, kind, role, cost, data, created_at)"
            " VALUES (?, ?, NULL, 'action_started', '', 0.0, ?, ?)",
            (
                self.session_id,
                self._next_seq,
                json.dumps({"action": action}, ensure_ascii=False, default=str),
                now,
            ),
        )
        self._next_seq += 1
        self._conn.commit()

    def apply_compression(self, *, cut_index: int, event_message: dict, agent_state: dict) -> None:
        """Mirror an in-memory compression rewrite inside one transaction.

        Precondition: the caller has NOT yet rewritten ``self.messages`` — the DB
        goes first, so a crash between the two leaves the post-rewrite state in
        the DB (the process' memory is lost either way).

        Mapping of old view positions (new view = old[:2] + [event] + old[cut:]):
        - ``[2, cut_index)``  → superseded (folded into the summary)
        - ``>= cut_index``    → ``p - cut_index + 3`` (kept turns start at position 3,
          after the event message; ``p + (3 - cut_index)``)
        - ``< 2`` (system/instance) → unchanged
        ``agent_state["last_trigger_pos"]`` is the pre-rewrite position of the
        consumed trigger message and is remapped the same way.
        """
        old_pos = agent_state.get("last_trigger_pos")
        if old_pos is not None:
            if old_pos >= cut_index:
                old_pos = old_pos - cut_index + 3
            elif old_pos >= 2:
                old_pos = None
        agent_state = {**agent_state, "last_trigger_pos": old_pos}
        now = time.time()
        cur = self._conn.cursor()
        try:
            cur.execute(
                "UPDATE messages SET view_pos = NULL"
                " WHERE session_id = ? AND kind = 'message' AND view_pos >= 2 AND view_pos < ?",
                (self.session_id, cut_index),
            )
            cur.execute(
                "UPDATE messages SET view_pos = view_pos - ? + 3"
                " WHERE session_id = ? AND kind = 'message' AND view_pos >= ?",
                (cut_index, self.session_id, cut_index),
            )
            cur.execute(
                "INSERT INTO messages (session_id, seq, view_pos, kind, role, cost, data, created_at)"
                " VALUES (?, ?, 2, 'message', ?, ?, ?, ?)",
                (
                    self.session_id,
                    self._next_seq,
                    str(event_message.get("role", "")),
                    _msg_cost(event_message),
                    json.dumps(event_message, ensure_ascii=False, default=str),
                    now,
                ),
            )
            self._next_seq += 1
            self._update_state_sql(cur, agent_state)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def update_state(self, agent_state: dict) -> None:
        """Refresh the state cache row (cheap; called outside message batches too)."""
        cur = self._conn.cursor()
        try:
            self._update_state_sql(cur, agent_state)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def finish(self, agent_state: dict) -> None:
        """Mark the session finished with its final exit status."""
        self.update_state({**agent_state, "status": "finished"})

    def _update_state_sql(self, cur: sqlite3.Cursor, agent_state: dict) -> None:
        tvars = agent_state.get("extra_template_vars") or {}
        cur.execute(
            "UPDATE sessions SET updated_at = ?, status = ?, exit_status = ?, submission = ?, task = ?,"
            " cost = ?, n_calls = ?, n_consecutive_format_errors = ?, compression_count = ?,"
            " last_trigger_pos = ?, extra_template_vars = ?, cost_last_confirmed = ?,"
            " start_time_epoch = ?, config_snapshot = ?,"
            " stall_streak = ?, stall_last_fp = ?, stall_warnings = ?"
            " WHERE session_id = ?",
            (
                time.time(),
                str(agent_state.get("status", "running")),
                str(agent_state.get("exit_status", "")),
                str(agent_state.get("submission", "")),
                str(tvars.get("task", "")),
                float(agent_state.get("cost", 0.0)),
                int(agent_state.get("n_calls", 0)),
                int(agent_state.get("n_consecutive_format_errors", 0)),
                int(agent_state.get("compression_count", 0)),
                agent_state.get("last_trigger_pos"),
                json.dumps(tvars, ensure_ascii=False, default=str),
                float(agent_state.get("cost_last_confirmed", 0.0)),
                float(agent_state.get("start_time_epoch") or 0.0),
                json.dumps(agent_state.get("config_snapshot", {}), ensure_ascii=False, default=str),
                int(agent_state.get("stall_streak", 0) or 0),
                agent_state.get("stall_last_fp"),
                int(agent_state.get("stall_warnings", 0) or 0),
                self.session_id,
            ),
        )

    # --- reads -------------------------------------------------------------

    def load_messages(self) -> list[dict]:
        """Load the current effective view (post-compression) as message dicts."""
        rows = self._conn.execute(
            "SELECT data FROM messages"
            " WHERE session_id = ? AND kind = 'message' AND view_pos IS NOT NULL"
            " ORDER BY view_pos",
            (self.session_id,),
        ).fetchall()
        return [json.loads(r[0]) for r in rows]

    # --- class-level helpers (used by the CLI before an agent exists) -------

    @classmethod
    def list_sessions(cls, db_path: str | Path) -> list[dict]:
        """Return all sessions, newest first. Missing DB raises FileNotFoundError."""
        path = Path(db_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Session DB not found: {path}")
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC")]
        finally:
            conn.close()

    @classmethod
    def load_session(cls, db_path: str | Path, session_id: str) -> dict[str, Any]:
        """Load one session's state row (messages excluded — the agent loads those)."""
        path = Path(db_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Session DB not found: {path}")
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            if row is None:
                raise ValueError(f"Session {session_id!r} not found in {path}")
            return cls._row_to_state(dict(row))
        finally:
            conn.close()
