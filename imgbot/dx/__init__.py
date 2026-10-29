# -*- coding: utf-8 -*-
"""dx 注入通道(阶段1): 大漠 dx 模式的复刻件。

宿主侧纯 ctypes, 注入件需先构建: uv run dx/native/build.py (需 zig)。
入口: DxHost(pid) -> attach/ping/hook_gl/capture/bye。
"""
from .host import CMD_HOOK_GL, DxHost, target_bitness


def mean_diff(a, b) -> float:
    """两帧平均绝对差(烟雾测试的帧差判据)。"""
    import numpy as np
    return float(abs(a.astype(int) - b.astype(int)).mean())


__all__ = ["DxHost", "target_bitness", "CMD_HOOK_GL", "mean_diff"]
