"""框架级异常类型。

插件的「数据层」抛出的可预期错误都继承这里，核心才能统一装上异常处理器：

    StoreError       数据层可预期的错误基类（→ 400）
      ├ NotFoundError  目标不存在（→ 404）
      └ ValidationError 入参/内容不合法（→ 400）

插件不要自己注册 handler，否则同一个错误在不同插件里会得到不同状态码。
"""

from __future__ import annotations


class StoreError(Exception):
    """数据层可预期的错误（路径非法、结构不对等）。"""


class NotFoundError(StoreError):
    """目标不存在。核心把它映射成 404。"""


class ValidationError(StoreError):
    """入参或内容不合法。核心把它映射成 400。"""
