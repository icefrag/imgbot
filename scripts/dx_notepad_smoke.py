# -*- coding: utf-8 -*-
"""阶段2 键鼠验收(消息型窗口): dx 通道向记事本 Edit 控件打字, WM_GETTEXT 读回。

这是"消息配方正确性"的硬验收 —— 与模拟器(自有输入通道, 走 adb)不同,
记事本是标准消息消费方, dx 键盘若在这里不通就是配方有 bug。
通过标准: 读回文本包含注入串。
"""
import ctypes
import subprocess
import sys
import time

from imgbot.dx import DxHost
from imgbot.dx.host import KEY_CHAR
from imgbot.dx.input import DxMouseKeyboard

u32 = ctypes.WinDLL("user32")
TEXT = "hello-dx-2026"


def main():
    p = subprocess.Popen(["notepad.exe"])
    time.sleep(1.5)
    from imgbot.win32.window import GameWindow
    win = GameWindow.find(class_name="Notepad")
    if not win:
        print("记事本窗口没找到")
        return 1
    # 经典记事本的编辑控件
    u32.FindWindowExW.restype = ctypes.c_void_p
    edit = u32.FindWindowExW(win.hwnd, None, "Edit", None)
    target = int(edit) if edit else win.hwnd
    print(f"notepad pid={p.pid} hwnd=0x{win.hwnd:X} edit=0x{target:X}")

    host = DxHost(p.pid)
    host.bind()
    try:
        host.bind_input(target)
        host.fake_active(True)          # 回归: 子类化后消息链必须仍完好
        mk = DxMouseKeyboard(host, target)
        mk.type_text(TEXT, per_char_delay=0.05)
        time.sleep(0.3)
        buf = ctypes.create_unicode_buffer(256)
        u32.SendMessageW(target, 0x000D, 256, buf)       # WM_GETTEXT
        got = buf.value
        print(f"WM_GETTEXT 读回: {got!r}")
        ok = TEXT in got
        print("键盘配方验收:", "PASS" if ok else "FAIL")
        if not ok:
            return 3
        # 清场后再验裸键路径: KEY_DOWN/UP + 注入件内手动 TranslateMessage 生成
        # WM_CHAR(x64 侧) —— 龙之谷聊天框打字场景的形状
        u32.SendMessageW(target, 0x000C, 0, "")          # WM_SETTEXT 清空
        RAW = "keysok"
        for ch in RAW:
            mk.key_tap(ord(ch.upper()))                  # 裸 VK, 不走 WM_CHAR
        time.sleep(0.3)
        buf2 = ctypes.create_unicode_buffer(256)
        u32.SendMessageW(target, 0x000D, 256, buf2)
        got2 = buf2.value
        print(f"裸键读回: {got2!r}")
        ok2 = got2 == RAW
        print("裸键(手动Translate)验收:", "PASS" if ok2 else "FAIL")
        if not ok2:
            return 4
        return 0
    finally:
        host.unbind()
        p.terminate()


if __name__ == "__main__":
    sys.exit(main())
