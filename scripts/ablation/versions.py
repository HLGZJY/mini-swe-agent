"""魔改6 消融版本定义：增量塔（v0 → v4）。

每个版本 = 相对 base.yaml 的 overlay。增量语义：
  v0 bash-only（上游等价行为：仅 bash 工具）
  v1 = v0 + 结构化 Tool Calling（4 工具），重试关闭
  v2 = v1 + 重试开启（默认行为：模型层 tenacity + 工具层 ToolSpec 瞬态重试）
  v3 = v2 + Context 压缩
  v4 = v3 + 停滞检测（全家桶）

重试关闭的实现：
  - 模型层：MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT=1（models/utils/retry.py:24，运行时读 env）
  - 工具层：patch "disable_tool_retry"（_run_one.py 把 read_file/grep/list_dir 的
    ToolSpec.max_retries 置 0；bash 从不声明 transient，无需处理）
"""

TOOLS_ALL = ["bash", "read_file", "grep", "list_dir"]
TOOLS_BASH_ONLY = ["bash"]
RETRY_OFF_ENV = {"MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT": "1"}

VERSIONS: dict[str, dict] = {
    "v0_bash_only": {
        "model": {"tools": TOOLS_BASH_ONLY},
        "env": dict(RETRY_OFF_ENV),
    },
    "v1_tc_noretry": {
        "model": {"tools": TOOLS_ALL},
        "env": dict(RETRY_OFF_ENV),
        "patch": ["disable_tool_retry"],
    },
    "v2_retry": {
        "model": {"tools": TOOLS_ALL},
    },
    "v3_compress": {
        "model": {"tools": TOOLS_ALL},
        "agent": {"compression_threshold_tokens": 24000},
    },
    "v4_stall": {
        "model": {"tools": TOOLS_ALL},
        "agent": {"compression_threshold_tokens": 24000, "stall_window": 3, "stall_max_warnings": 2},
    },
}

VERSION_ORDER = list(VERSIONS)
