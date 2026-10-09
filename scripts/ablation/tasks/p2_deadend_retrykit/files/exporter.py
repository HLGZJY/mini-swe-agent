"""轻量 metrics exporter：把内存行格式化为 collector 行协议，并带重试推送。

历史：`retry_kit` 原是团队内部 pip 包，仓库从 monorepo 拆分出去后没有随仓分发，
但本模块还在引用它（见 import）。CI 因此一直红着。
"""

from __future__ import annotations

from retry_kit import backoff  # 内部通用重试库（拆仓后未随仓分发，见 README）


def format_payload(rows) -> str:
    """把 [(ts, name, value)] 格式化为 collector 行协议：每行 `name value ts`。

    - 按 (name, ts) 排序输出；
    - 空输入返回空字符串；
    - 结尾带换行（collector 协议要求），空输入除外。
    """
    lines = []
    for ts, name, value in sorted(rows, key=lambda r: (r[1], r[0])):
        lines.append(f"{name} {value} {int(ts)}")
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


@backoff(times=3, delay=0.5)
def send(session, url: str, payload: str):
    """推送 payload 到 collector；网络抖动时由 backoff 装饰器自动重试。

    语义约定：最多尝试 times 次（含首次），每次间隔 delay 秒；全部失败则把
    最后一次的异常抛给调用方。
    """
    resp = session.post(url, data=payload)
    resp.raise_for_status()
    return resp.status_code
