# -*- coding: utf-8 -*-
"""假激活过滤验收(32 位链路): 子类化模拟器主窗后直接投递失焦/失活消息,
sub_proc 应吞掉它们(不转发), msg_filtered 精确计数。

这不需要目标消费输入(模拟器消息免疫也没关系), 只验子类化拦截本身。
通过标准: 投 3 条应吞消息 → 计数 +3; 投 WM_NULL(普通消息) → 计数不变。
"""
import ctypes
import sys
import time

from imgbot.dx import DxHost
from imgbot.win32.window import GameWindow

u32 = ctypes.WinDLL("user32")
WM_NULL, WM_ACTIVATE, WM_KILLFOCUS = 0x0000, 0x0006, 0x0008
WA_INACTIVE = 0


def post(hwnd, msg, wp, lp):
    res = ctypes.c_ulong()
    u32.SendMessageTimeoutW(hwnd, msg, wp, lp, 0x0002, 2000, ctypes.byref(res))


def main():
    win = GameWindow.find(class_name="LDPlayerMainFrame")
    if not win:
        print("模拟器没开")
        return 1
    host = DxHost(win.pid())
    host.bind()
    try:
        host.bind_input(win.hwnd)
        host.fake_active(True)
        base = host.msg_filtered
        post(win.hwnd, WM_KILLFOCUS, 0, 0)                       # 应吞
        post(win.hwnd, WM_ACTIVATE, WA_INACTIVE, 0)              # 应吞(低字=0)
        post(win.hwnd, 0x001C, 0, 0)                             # WM_ACTIVATEAPP w=0 应吞
        time.sleep(0.1)
        after3 = host.msg_filtered
        post(win.hwnd, WM_NULL, 0, 0)                            # 普通消息, 转发不计数
        time.sleep(0.1)
        after_null = host.msg_filtered
        print(f"吞消息计数: {base} -> {after3} (+{after3 - base}, 期望 +3)")
        print(f"投 WM_NULL 后: {after_null} (期望不变)")
        ok = after3 - base == 3 and after_null == after3
        print("假激活过滤验收:", "PASS" if ok else "FAIL")
        return 0 if ok else 3
    finally:
        try:
            host.unbind()
        except TimeoutError:
            pass


if __name__ == "__main__":
    sys.exit(main())
