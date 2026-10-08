"""Tests for the tool registry: schema generation, validation, error self-healing."""

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from minisweagent.exceptions import FormatError
from minisweagent.models.utils.actions_toolcall import BASH_TOOL, parse_toolcall_actions
from minisweagent.models.utils.tool_registry import ToolRegistry, ToolSpec, get_default_registry


class _Args(BaseModel):
    x: int = Field(description="an int")


def _make_tc(name: str, arguments: str, call_id: str = "call_1") -> SimpleNamespace:
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


class TestRegistryBasics:
    def test_default_registry_contains_builtin_tools(self):
        assert get_default_registry().names() == ["bash", "read_file", "grep", "list_dir"]

    def test_duplicate_registration_rejected(self):
        registry = ToolRegistry()
        registry.register(ToolSpec(name="t", description="", args_model=_Args, execute=lambda a: "ok"))
        with pytest.raises(ValueError, match="Duplicate tool name"):
            registry.register(ToolSpec(name="t", description="", args_model=_Args, execute=lambda a: "ok"))

    def test_unknown_name_lookup(self):
        assert get_default_registry().get("nope") is None

    def test_advertise_unknown_tool_raises(self):
        with pytest.raises(KeyError, match="not registered"):
            get_default_registry().to_openai_tools(["bash", "nope"])


class TestSchemaFormats:
    def test_openai_format_bash_semantically_equal_to_bash_tool_constant(self):
        """The registry-generated bash definition must stay semantically equal to the
        hand-written BASH_TOOL constant that the rest of the codebase still references."""
        (spec,) = get_default_registry().to_openai_tools(["bash"])
        assert spec["type"] == "function"
        assert spec["function"]["name"] == BASH_TOOL["function"]["name"] == "bash"
        assert spec["function"]["description"] == BASH_TOOL["function"]["description"]
        params = spec["function"]["parameters"]
        assert params["type"] == "object"
        assert params["required"] == ["command"]
        assert params["properties"]["command"]["type"] == "string"
        # title keys are stripped for a clean tool-API schema
        assert "title" not in params
        assert "title" not in params["properties"]["command"]

    def test_response_api_format_is_flat(self):
        (spec,) = get_default_registry().to_response_api_tools(["bash"])
        assert spec["type"] == "function"
        assert spec["name"] == "bash"
        assert "function" not in spec
        assert spec["parameters"]["required"] == ["command"]

    def test_structured_tool_schema_includes_constraints(self):
        (spec,) = get_default_registry().to_openai_tools(["grep"])
        props = spec["function"]["parameters"]["properties"]
        assert props["max_results"]["minimum"] == 1
        assert props["max_results"]["maximum"] == 500
        assert props["pattern"]["type"] == "string"


class TestExecuteAction:
    def test_successful_execution(self, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("line1\nline2\nline3\n", encoding="utf-8")
        result = get_default_registry().execute_action(
            {"tool": "read_file", "args": {"path": str(f), "start_line": 2, "end_line": 3}}
        )
        assert result["returncode"] == 0
        assert "line2" in result["output"] and "line3" in result["output"]
        assert "line1" not in result["output"]

    def test_schema_validation_failure_returns_observation_not_exception(self, tmp_path):
        """THE core self-healing case: wrong argument type -> error observation with schema, no raise."""
        f = tmp_path / "hello.txt"
        f.write_text("content", encoding="utf-8")
        result = get_default_registry().execute_action(
            {"tool": "read_file", "args": {"path": str(f), "start_line": "abc"}}
        )
        assert result["returncode"] == 1
        assert "failed schema validation" in result["output"]
        assert "Nothing was executed" in result["output"]
        # the expected schema is embedded so the model can fix itself without another round trip
        assert '"start_line"' in result["output"]
        assert result["exception_info"] and "NOT executed" in result["exception_info"]

    def test_missing_required_argument(self, tmp_path):
        result = get_default_registry().execute_action({"tool": "read_file", "args": {}})
        assert result["returncode"] == 1
        assert "path" in result["output"]  # pydantic names the missing field

    def test_numeric_string_is_coerced_not_rejected(self, tmp_path):
        """Documented pydantic gotcha: "2" -> 2 coercion succeeds. Wrong-type demos must use
        errors coercion cannot fix (e.g. "abc" for int), or missing required fields."""
        f = tmp_path / "hello.txt"
        f.write_text("a\nb\nc\n", encoding="utf-8")
        result = get_default_registry().execute_action(
            {"tool": "read_file", "args": {"path": str(f), "start_line": "2"}}
        )
        assert result["returncode"] == 0
        assert "b" in result["output"]

    def test_tool_exception_returns_observation(self, tmp_path):
        result = get_default_registry().execute_action(
            {"tool": "read_file", "args": {"path": str(tmp_path / "missing.txt")}}
        )
        assert result["returncode"] == 1
        assert "FileNotFoundError" in result["output"]

    def test_unknown_tool_dispatch(self):
        result = get_default_registry().execute_action({"tool": "nope", "args": {}})
        assert result["returncode"] == 1
        assert "unknown or non-executable" in result["output"]


class TestParseIntegration:
    def test_structured_tool_call_parsed_to_tool_action(self):
        (action,) = parse_toolcall_actions(
            [_make_tc("read_file", '{"path": "x.txt", "start_line": 2}')],
            format_error_template="{{ error }}",
        )
        assert action == {"tool": "read_file", "args": {"path": "x.txt", "start_line": 2}, "tool_call_id": "call_1"}

    def test_bash_action_shape_unchanged(self):
        (action,) = parse_toolcall_actions([_make_tc("bash", '{"command": "ls"}')], format_error_template="{{ error }}")
        assert action == {"command": "ls", "tool_call_id": "call_1"}

    def test_unknown_tool_still_format_error(self):
        # FormatError carries messages in .messages (str(exc) is empty), so assert on content
        with pytest.raises(FormatError) as exc:
            parse_toolcall_actions([_make_tc("nope", "{}")], format_error_template="{{ error }}")
        assert "Unknown tool" in exc.value.messages[0]["content"]

    def test_bash_missing_command_still_format_error(self):
        """Upstream fixed the old KeyError via the args check (#760); behavior preserved."""
        with pytest.raises(FormatError) as exc:
            parse_toolcall_actions([_make_tc("bash", '{"path": "x"}')], format_error_template="{{ error }}")
        assert "Missing 'command'" in exc.value.messages[0]["content"]

    def test_structured_tool_non_object_args_is_format_error(self):
        with pytest.raises(FormatError) as exc:
            parse_toolcall_actions([_make_tc("read_file", '"just a string"')], format_error_template="{{ error }}")
        assert "expected a JSON object" in exc.value.messages[0]["content"]

    def test_mixed_bash_and_structured_tools(self):
        actions = parse_toolcall_actions(
            [
                _make_tc("bash", '{"command": "echo hi"}', call_id="c1"),
                _make_tc("list_dir", '{"path": "."}', call_id="c2"),
            ],
            format_error_template="{{ error }}",
        )
        assert actions[0] == {"command": "echo hi", "tool_call_id": "c1"}
        assert actions[1] == {"tool": "list_dir", "args": {"path": "."}, "tool_call_id": "c2"}
        assert json.dumps(actions)  # all actions are json-serializable for trajectories
