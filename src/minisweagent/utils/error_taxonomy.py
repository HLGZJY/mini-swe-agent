"""执行失败错误分类（魔改 2）：环境/工具层的第三条错误通道。

分层（与 FormatError、Schema 校验失败互不混用，见魔改 1/2 笔记）：
- FormatError：协议坏（输出非合法 tool_call / 未知工具名），整轮报废，连续计数退出；
- Schema 校验失败（invalid_params）：参数错，观察回填自愈，执行前拦截，永不重试；
- 本模块：执行失败分类——瞬态类可自动重试（由 ToolSpec.transient_errors 声明），
  永久类回填模型触发 Replan；重试预算耗尽走受控终止。

分类结果写入观察 dict 的 ``error_class`` 键（成功时不带该键——「缺 = 成功」），
供轨迹离线统计（消融评测「Retry 自愈次数占比」直接数该字段）；模型可读的
分类与引导文案拼进 ``exception_info``，走既有 ``<exception>`` 渲染通道，
不新增观察模板副本（模板全仓有 12 处副本且用 StrictUndefined 渲染）。
"""

import subprocess

ERROR_INVALID_PARAMS = "invalid_params"
"""参数不满足工具 Schema（魔改 1 校验层，执行前拦截，永不进入重试）。"""

ERROR_UNKNOWN_TOOL = "unknown_tool"
"""工具名不在 Registry 或不可执行（分派失败，永久）。"""

ERROR_TIMEOUT = "timeout"
"""命令/工具超时。不是瞬态错误：非终止命令重试是纯烧预算，永不自动重试。"""

ERROR_PERMISSION = "permission"
"""权限拒绝（PermissionError）。只读工具声明重试它（Windows 文件锁是典型瞬态）。"""

ERROR_ENV_UNAVAILABLE = "env_unavailable"
"""环境/资源不可用（其余 OSError，如路径不存在、磁盘问题）。"""

ERROR_TOOL_ERROR = "tool_error"
"""工具自身异常（其余一切 Exception）。"""

# 永久类错误的模型引导文案：拼进 exception_info，模型看得到、轨迹里可 grep
_GUIDANCE = {
    ERROR_TIMEOUT: (
        "Error class: timeout. Do NOT simply rerun the same command unchanged: "
        "a non-terminating command will time out again. Split the work into "
        "smaller steps or change your approach."
    ),
    ERROR_PERMISSION: (
        "Error class: permission. Retrying the identical call will fail again. "
        "Change the approach (different path or strategy) or report the blocker."
    ),
    ERROR_ENV_UNAVAILABLE: (
        "Error class: env_unavailable. The environment or resource appears "
        "unavailable. Verify the path/state first or change the approach; do not "
        "repeat the identical call."
    ),
}


def guidance_for(error_class: str) -> str:
    """Model-facing hint for permanent error classes (empty string if none)."""
    return _GUIDANCE.get(error_class, "")


def classify_exception(e: BaseException) -> str:
    """Map an execution exception to its error class.

    Order matters: PermissionError is an OSError subclass and must be checked
    before the OSError catch-all.
    """
    if isinstance(e, subprocess.TimeoutExpired):
        return ERROR_TIMEOUT
    if isinstance(e, PermissionError):
        return ERROR_PERMISSION
    if isinstance(e, OSError):
        return ERROR_ENV_UNAVAILABLE
    return ERROR_TOOL_ERROR
