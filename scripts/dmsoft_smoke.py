# -*- coding: utf-8 -*-
"""DmSoft 壳真机验收: 大漠姿势跑通 模拟器(dx 图色) + 记事本(windows 键鼠)。

验收标准: 每步打印 PASS 行; 任何一步异常即失败。
用法: uv run scripts/dmsoft_smoke.py
"""
import subprocess
import sys
import time
from pathlib import Path

LOG = Path("logs")


def emulator_dx():
    from imgbot.dmsoft import DmSoft
    from imgbot.win32.window import GameWindow
    win = GameWindow.find(class_name="LDPlayerMainFrame")
    if not win:
        print("跳过: 模拟器没开")
        return True
    dm = DmSoft()
    assert dm.BindWindow(win.hwnd, "dx.graphic.opengl", "windows", "windows", 0) == 1
    print("PASS BindWindow(dx.graphic.opengl, windows, windows)")
    dm.MoveTo(270, 400)
    col = dm.GetColor(270, 400)
    assert len(col) == 6 and int(col, 16) >= 0
    print(f"PASS GetColor(270,400) = {col}")
    dm.Capture(0, 0, 540, 960, str(LOG / "dmsoft_cap.png"))
    print("PASS Capture -> logs/dmsoft_cap.png")
    x, y = dm.FindColor(0, 0, 540, 960, "FFFFFF", 0.8)
    print(f"PASS FindColor 白色: ({x},{y}) ({'找到' if x >= 0 else '无白色像素, 也算通'})")
    dm.UnBindWindow()
    print("PASS UnBindWindow")
    return True


def notepad_windows():
    import ctypes
    from imgbot.dmsoft import DmSoft
    app = subprocess.Popen(["notepad.exe"])
    try:
        time.sleep(1.5)
        dm = DmSoft()
        hwnd = dm.FindWindow("Notepad")
        assert hwnd, "没找到记事本"
        u32 = ctypes.WinDLL("user32")
        u32.FindWindowExW.restype = ctypes.c_void_p
        edit = int(u32.FindWindowExW(hwnd, None, "Edit", None) or hwnd)
        assert dm.BindWindow(edit, "gdi", "windows", "windows", 0) == 1
        print("PASS BindWindow(gdi, windows, windows) @记事本Edit")
        dm.SendString(edit, "dmsoft-2026")
        time.sleep(0.3)
        buf = ctypes.create_unicode_buffer(256)
        u32.SendMessageW(edit, 0x000D, 256, buf)
        assert "dmsoft-2026" in buf.value, f"读回 {buf.value!r}"
        print(f"PASS SendString + WM_GETTEXT 读回: {buf.value!r}")
        # MoveTo/GetCursorPos: 虚拟光标(客户区坐标)+ 越界拒绝(大漠反编译对齐)
        assert dm.MoveTo(30, 40) == 1
        cx, cy = dm.GetCursorPos()
        assert (cx, cy) == (30, 40), f"虚拟光标 {cx},{cy} != 30,40"
        w_, h_ = dm.GetClientSize(edit)
        assert dm.MoveTo(w_ + 5, 10) == 0, "越界 MoveTo 应拒绝返回 0"
        cx2, cy2 = dm.GetCursorPos()
        assert (cx2, cy2) == (30, 40), "越界移动后虚拟光标不应变"
        print(f"PASS MoveTo/GetCursorPos 虚拟光标({cx},{cy}) + 越界拒绝")
        dm.UnBindWindow()
        # normal 纯前台: 截屏图色(裁客户区)+ SendInput 真事件(动真光标/真键盘)
        import ctypes.wintypes
        assert dm.BindWindow(hwnd, "normal", "normal", "normal", 0) == 1
        print("PASS BindWindow(normal, normal, normal) 纯前台零注入")
        dm.MoveTo(60, 60)                     # 同时把目标带前台(截屏防遮挡)
        sx, sy = dm.ClientToScreen(hwnd, 60, 60)
        pt = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        assert (pt.x, pt.y) == (sx, sy), f"真光标 {pt.x},{pt.y} != 客户区(60,60)→屏幕 {sx},{sy}"
        print(f"PASS MoveTo 真光标落点 ({pt.x},{pt.y})")
        col_n = dm.GetColor(30, 40)
        dm.UnBindWindow()
        assert dm.BindWindow(hwnd, "gdi", "windows", "windows", 0) == 1
        col_g = dm.GetColor(30, 40)           # 同窗口同点, gdi(PrintWindow) 对拍
        dm.UnBindWindow()
        assert col_n == col_g, f"normal 裁剪与 gdi 不一致: {col_n} != {col_g}"
        print(f"PASS normal 图色客户区裁剪: GetColor(30,40)={col_n} == gdi")
        assert dm.BindWindow(hwnd, "normal", "normal", "normal", 0) == 1
        dm.MoveTo(60, 60)
        dm.LeftClick()                        # 真人流程: 先点击定位光标(兼验点击)
        dm.SendString(hwnd, "fg26")           # UNICODE 注字符(IME 免疫)
        dm.KeyPress(0x74)                     # F5 裸键(非文本键不经 IME): 记事本插时间
        time.sleep(0.4)
        buf2 = ctypes.create_unicode_buffer(256)
        u32.SendMessageW(edit, 0x000D, 256, buf2)
        assert "fg26" in buf2.value and ":" in buf2.value, \
            f"前台打字读回 {buf2.value!r}(UNICODE 应含 fg26, F5 应含时间冒号)"
        print(f"PASS 前台键盘: UNICODE 打字+F5 裸键 读回 {buf2.value!r}")
        dm.UnBindWindow()
        return True
    finally:
        app.terminate()


def main():
    LOG.mkdir(exist_ok=True)
    ok = emulator_dx() and notepad_windows()
    print("DmSoft 壳验收:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
