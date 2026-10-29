# -*- coding: utf-8 -*-
"""一次性诊断: 模拟器为什么无视 dx 消息输入。

检查项:
1. 目标进程是否注册了 Raw Input(只有注入件内能查) → 决定要不要走 raw input 补丁;
2. 假激活(WM_ACTIVATE+WM_SETFOCUS)后再拖拽是否生效 → 激活门槛假设;
3. MainFrame vs RenderWindow 两个窗口分别试。
"""
import ctypes
import sys
import time

import numpy as np

from imgbot.dx import DxHost, mean_diff
from imgbot.dx.host import M_LDOWN, M_LUP, M_MOVE
from imgbot.win32.window import GameWindow

u32 = ctypes.WinDLL("user32")
WM_ACTIVATE, WM_SETFOCUS = 0x0006, 0x0007


def drag(host, w, h):
    cx = w // 2
    pts = [(cx, int(h * .75)), (cx, int(h * .65)), (cx, int(h * .5)),
           (cx, int(h * .4)), (cx, int(h * .25))]
    host.mouse(*pts[0], M_MOVE)
    time.sleep(0.05)
    host.mouse(*pts[0], M_LDOWN)
    for p in pts[1:]:
        host.mouse(*p, M_MOVE)
        time.sleep(0.04)
    host.mouse(*pts[-1], M_LUP)


def try_drag(host, hwnd, label, frame0):
    host.bind_input(hwnd)
    drag(host, frame0.shape[1], frame0.shape[0])
    time.sleep(1.2)
    f = host.capture(fresh_wait=1.5)
    print(f"  {label}: 帧差 {mean_diff(frame0, f):.1f}")


def main():
    outer = GameWindow.find(class_name="LDPlayerMainFrame")
    inner = GameWindow.find(class_name="RenderWindow", parent=outer.hwnd)
    host = DxHost(outer.pid())
    host.bind()
    try:
        host.hook_gl()
        time.sleep(0.3)
        info = host.rawinfo()
        print(f"  注册 raw input 设备数={info['devices']}, "
              f"有目标窗口的={info['with_target']}, 目标 hwnd=0x{info['target_hwnd']:X}")
        f0 = host.capture(fresh_wait=2.0)
        print(f"基线帧 {f0.shape[1]}x{f0.shape[0]}")
        try_drag(host, outer.hwnd, "拖拽→MainFrame", f0)
        try_drag(host, inner.hwnd if inner else outer.hwnd, "拖拽→RenderWindow", f0)
        # 假激活两个窗口后再试
        for hwnd in (inner.hwnd if inner else outer.hwnd, outer.hwnd):
            u32.PostMessageW(hwnd, WM_ACTIVATE, 1, 0)
            u32.PostMessageW(hwnd, WM_SETFOCUS, 0, 0)
        time.sleep(0.5)
        f0 = host.capture(fresh_wait=1.0)
        try_drag(host, inner.hwnd if inner else outer.hwnd, "假激活后拖拽→RenderWindow", f0)
        try_drag(host, outer.hwnd, "假激活后拖拽→MainFrame", f0)
    finally:
        try:
            host.unbind()
        except TimeoutError:
            pass


if __name__ == "__main__":
    sys.exit(main())
