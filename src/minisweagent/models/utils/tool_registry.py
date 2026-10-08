"""Tool Registry: 统一注册工具的名称 / 描述 / JSON Schema / 执行函数。

设计要点（对应魔改 1 笔记）：
- **Schema 单一事实源**：每个工具的参数模型是一个 pydantic ``BaseModel``。
  给 LM 广播的 JSON Schema 用 ``model_json_schema()`` 导出，本地校验用同一个模型
  ``model_validate()`` —— 描述与校验永远同源，不存在「Schema 改了校验没跟上」的漂移。
- **两类工具**：``execute=None`` 的工具（bash）由 Agent 的环境执行，action 保持
  ``{"command": ..., "tool_call_id": ...}`` 旧形状；其余工具在 Agent 进程内直接执行，
  action 形状为 ``{"tool": ..., "args": ..., "tool_call_id": ...}``。
- **错误自愈**：参数不满足 Schema **不抛异常**——校验错误（含期望 Schema）作为该
  tool_call 的观察输出回传给模型，模型下一轮自己修正。这与 bash 命令失败（非零
  exit code）回填观察的语义同构；协议级错误（输出不是合法 tool_call / 未知工具名）
  仍走 FormatError，两者不混用。
- 仅支持扁平（非嵌套）参数模型：``_clean_schema`` 不处理 ``$defs``。
"""

import json
import re
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError


@dataclass
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    """Pydantic model describing the tool arguments. Doubles as the JSON Schema source
    (``model_json_schema()``) and the local validator (``model_validate()``)."""
    execute: Callable[[BaseModel], str] | None = None
    """None = the tool is executed by the agent's environment (e.g. bash in a shell).
    Otherwise: called with the validated args model instance, returns the observation text."""
    path_fields: tuple[str, ...] = ()
    """Names of args fields holding filesystem paths. When ``base_dir`` is passed to
    :meth:`ToolRegistry.execute_action`, relative values in these fields are resolved
    against it — in-process tools must operate on the same directory as the agent's
    environment, whose working directory the tool call knows nothing about otherwise."""


def _clean_schema(schema: dict) -> dict:
    """Strip pydantic-internal ``title`` keys for a clean tool-API JSON Schema."""
    out = {k: v for k, v in schema.items() if k != "title"}
    props = out.get("properties")
    if isinstance(props, dict):
        out["properties"] = {name: {k: v for k, v in spec.items() if k != "title"} for name, spec in props.items()}
    return out


class ToolRegistry:
    """Registry of :class:`ToolSpec` objects, keyed by tool name."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Duplicate tool name: '{spec.name}'")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def _specs(self, names: list[str] | None) -> list[ToolSpec]:
        if names is None:
            return list(self._tools.values())
        missing = [n for n in names if n not in self._tools]
        if missing:
            raise KeyError(f"Tools not registered: {missing}. Known: {self.names()}")
        return [self._tools[n] for n in names]

    def to_openai_tools(self, names: list[str] | None = None) -> list[dict]:
        """Chat-completions function-calling format: ``{"type": "function", "function": {...}}``."""
        return [
            {
                "type": "function",
                "function": {
                    "name": s.name,
                    "description": s.description,
                    "parameters": _clean_schema(s.args_model.model_json_schema()),
                },
            }
            for s in self._specs(names)
        ]

    def to_response_api_tools(self, names: list[str] | None = None) -> list[dict]:
        """Responses API flat format: ``{"type": "function", "name": ...}`` (no nested "function")."""
        return [
            {
                "type": "function",
                "name": s.name,
                "description": s.description,
                "parameters": _clean_schema(s.args_model.model_json_schema()),
            }
            for s in self._specs(names)
        ]

    def execute_action(self, action: dict, *, base_dir: str | None = None) -> dict:
        """Execute a structured tool action ``{"tool": name, "args": {...}}``.

        ``base_dir`` is the working directory of the agent's environment; relative
        values in the tool's ``path_fields`` are resolved against it before validation.

        Returns an observation dict shaped like ``env.execute()`` outputs
        (``{"output": str, "returncode": int, "exception_info": str | None}``) so the
        existing observation template and tool_call_id backfill work unchanged.

        Schema validation failures and tool exceptions are returned as observations
        (error self-healing), never raised: the model sees the error and can retry.
        """
        spec = self.get(action.get("tool", ""))
        if spec is None or spec.execute is None:
            return {
                "output": f"Error: unknown or non-executable tool '{action.get('tool')}'.",
                "returncode": 1,
                "exception_info": "tool dispatch failed; action was NOT executed",
            }
        args = dict(action.get("args") or {})
        if base_dir:
            for field_name in spec.path_fields:
                value = args.get(field_name)
                if isinstance(value, str) and value and not Path(value).is_absolute():
                    args[field_name] = str(Path(base_dir) / value)
        try:
            validated = spec.args_model.model_validate(args)
        except ValidationError as e:
            expected = json.dumps(_clean_schema(spec.args_model.model_json_schema()), ensure_ascii=False)
            return {
                "output": (
                    f"Error: arguments for tool '{spec.name}' failed schema validation.\n"
                    f"{e}\n"
                    f"Expected arguments schema: {expected}\n"
                    "Nothing was executed. Fix the arguments and call the tool again."
                ),
                "returncode": 1,
                "exception_info": "tool arguments failed schema validation; action was NOT executed",
            }
        try:
            return {"output": spec.execute(validated), "returncode": 0, "exception_info": None}
        except Exception as e:
            return {
                "output": f"Error: tool '{spec.name}' raised {type(e).__name__}: {e}",
                "returncode": 1,
                "exception_info": traceback.format_exc(),
            }


# --- Builtin tools -----------------------------------------------------------


class BashArgs(BaseModel):
    command: str = Field(description="The bash command to execute")


class ReadFileArgs(BaseModel):
    path: str = Field(description="Path to the file to read")
    start_line: int | None = Field(default=None, description="1-based first line to read (default: 1)")
    end_line: int | None = Field(default=None, description="1-based last line to read, inclusive (default: EOF)")


MAX_READ_BYTES = 100_000
"""Read at most this many bytes when the caller gave no line window."""


def _read_file(args: ReadFileArgs) -> str:
    data = Path(args.path).read_bytes()  # OSError bubbles up -> error observation, model can retry
    truncated = len(data) > MAX_READ_BYTES
    if truncated:
        data = data[:MAX_READ_BYTES]
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    start = max(args.start_line or 1, 1)
    end = args.end_line if args.end_line is not None else len(lines)
    window = lines[start - 1 : end]
    numbered = "\n".join(f"{lineno:>6}\t{line}" for lineno, line in enumerate(window, start=start))
    if truncated:
        numbered += f"\n... (output truncated at {MAX_READ_BYTES} bytes; use start_line/end_line to read more)"
    if not numbered:
        numbered = f"(no lines in range {start}-{end}; file has {len(lines)} lines)"
    return numbered


class GrepArgs(BaseModel):
    pattern: str = Field(description="Python regular expression to search for")
    path: str = Field(default=".", description="File or directory to search in (default: current directory)")
    max_results: int = Field(default=50, ge=1, le=500, description="Maximum number of matching lines to return")
    ignore_case: bool = Field(default=False, description="Case-insensitive search")


MAX_GREP_FILE_BYTES = 1_000_000
"""Skip files larger than this when searching."""


def _grep(args: GrepArgs) -> str:
    rx = re.compile(args.pattern, re.IGNORECASE if args.ignore_case else 0)  # re.error -> error observation
    root = Path(args.path)
    if root.is_file():
        files = [root]
    else:
        files = sorted(p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts)
    results: list[str] = []
    for path in files:
        try:
            if path.stat().st_size > MAX_GREP_FILE_BYTES:
                continue
            with open(path, "rb") as f:
                head = f.read(1024)
                if b"\x00" in head:  # binary file
                    continue
                f.seek(0)
                for lineno, raw in enumerate(f, start=1):
                    line = raw.decode("utf-8", errors="replace")
                    if rx.search(line):
                        results.append(f"{path}:{lineno}: {line.rstrip()[:200]}")
                        if len(results) >= args.max_results:
                            return "\n".join(results)
        except OSError:
            continue  # unreadable file: skip, keep searching
    return "\n".join(results) if results else "No matches found."


class ListDirArgs(BaseModel):
    path: str = Field(default=".", description="Directory to list (default: current directory)")


def _list_dir(args: ListDirArgs) -> str:
    entries = sorted(Path(args.path).iterdir(), key=lambda e: (not e.is_dir(), e.name))
    lines = []
    for entry in entries:
        if entry.is_dir():
            lines.append(f"d         {entry.name}/")
        else:
            lines.append(f"- {entry.stat().st_size:>8} {entry.name}")
    return "\n".join(lines) if lines else "(empty directory)"


_DEFAULT_REGISTRY = ToolRegistry()

_DEFAULT_REGISTRY.register(ToolSpec(name="bash", description="Execute a bash command", args_model=BashArgs))
_DEFAULT_REGISTRY.register(
    ToolSpec(
        name="read_file",
        description="Read a text file, with optional 1-based line window",
        args_model=ReadFileArgs,
        execute=_read_file,
        path_fields=("path",),
    )
)
_DEFAULT_REGISTRY.register(
    ToolSpec(
        name="grep",
        description="Search file contents with a regular expression",
        args_model=GrepArgs,
        execute=_grep,
        path_fields=("path",),
    )
)
_DEFAULT_REGISTRY.register(
    ToolSpec(
        name="list_dir",
        description="List a directory's entries with types and sizes",
        args_model=ListDirArgs,
        execute=_list_dir,
        path_fields=("path",),
    )
)


def get_default_registry() -> ToolRegistry:
    """Return the process-wide default registry (bash + builtin structured tools)."""
    return _DEFAULT_REGISTRY
