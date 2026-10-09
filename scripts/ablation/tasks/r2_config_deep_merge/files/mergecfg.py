"""配置分层合并（服务配置 base < 环境 < 实例 三层覆盖用）。

契约：
- deep_merge(base, override) -> 新 dict（不得修改入参）
- override 优先；两边同为 dict 时**逐层递归**合并（任意深度）；
- 其他类型（标量/列表/None）整体覆盖；
- override 有而 base 无的键正常加入；base 有而 override 无的键保留。
"""

from __future__ import annotations


def deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **value}
        else:
            out[key] = value
    return out
