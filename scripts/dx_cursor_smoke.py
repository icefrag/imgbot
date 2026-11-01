# -*- coding: utf-8 -*-
"""虚拟光标钩验收(GetCursorPos 读位型游戏输入): 靶 kspoll32(每帧轮询 GetCursorPos)。

两阶段:
  阶段1 不挂钩: 注入鼠标移动 —— 靶读到的仍是真实光标位置(坐实: 消息注入
    改不了 GetCursorPos 读数, "能移不能点"病根之一);
  阶段2 挂 mouse_lock: 同样一次移动 —— 靶读数变成注入位(客户区坐标经
    ClientToScreen 的屏幕坐标), 拔钩后回到真实位。
通过标准: 阶段1 读数≠注入位; 阶段2 读数=注入位(±1px); 复原段回到阶段1 读数。
另含 windows2 全链: DmSoft.BindWindow(mouse="windows2") 自动开锁, MoveTo 后读数=注入位。
"""
import ctypes
import re
import subprocess
import sys
import time
from pathlib import Path

from imgbot.dx.host import DxHost, M_MOVE

u32 = ctypes.WinDLL("user32")
PT = (200, 40)    # 须在 kspoll 客户区内(~384x81): DmSoft.MoveTo 越界拒绝是大漠口径, 别误判


def read_cursor(hwnd: int):
    buf = ctypes.create_unicode_buffer(160)
    u32.GetWindowTextW(hwnd, buf, 160)
    m = re.match(r"down=\d+ latch=\d+ gks=\d+ kd=\d+ char=\d+ gcx=(-?\d+) gcy=(-?\d+)",
                 buf.value)
    return (int(m.group(1)), int(m.group(2))) if m else None


def wait_read(hwnd: int, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        c = read_cursor(hwnd)
        if c:
            return c
        time.sleep(0.05)
    return None


def real_pos():
    pt = ctypes.wintypes.POINT()
    u32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def near(a, b, tol=1):
    return a is not None and b is not None and abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def main():
    import ctypes.wintypes  # noqa: F401(real_pos 用)
    exe = Path(__file__).resolve().parent.parent / "imgbot" / "dx" / "bin" / "kspoll32.exe"
    p = subprocess.Popen([str(exe)])
    time.sleep(1.0)
    u32.FindWindowW.restype = ctypes.c_void_p
    hwnd = int(u32.FindWindowW("imgbot_kspoll", None) or 0)
    if not hwnd:
        print("kspoll 窗口没找到(先 uv run imgbot/dx/native/build.py 重编靶)")
        return 1
    print(f"kspoll pid={p.pid} hwnd=0x{hwnd:X}")

    rc = u32.GetWindowRect(hwnd, None)  # 占位防误用; 真正矩形走下面
    import ctypes.wintypes as wt
    rect = wt.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(rect))
    # 客户区原点(注入位按客户区坐标给, 期望读数=屏幕坐标)
    pt0 = wt.POINT(0, 0)
    u32.ClientToScreen(hwnd, ctypes.byref(pt0))
    expect = (pt0.x + PT[0], pt0.y + PT[1])

    host = DxHost(p.pid)
    host.bind()
    try:
        host.bind_input(hwnd)
        base = wait_read(hwnd)                      # 未注入时靶读数 ≈ 真实光标
        host.mouse(PT[0], PT[1], M_MOVE)            # 阶段1: 无锁
        time.sleep(0.4)
        c1 = wait_read(hwnd)
        print(f"阶段1(无锁): 靶读={c1} 真实={real_pos()} 期望≠{expect}")
        host.mouse_lock(True)                       # 阶段2: 开锁
        host.mouse(PT[0], PT[1], M_MOVE)
        time.sleep(0.4)
        c2 = wait_read(hwnd)
        print(f"阶段2(锁定): 靶读={c2} 期望={expect}")
        host.mouse_lock(False)                      # 复原: 拔钩
        time.sleep(0.4)
        c3 = wait_read(hwnd)
        print(f"复原(拔锁): 靶读={c3}")

        ok1 = c1 is not None and not near(c1, expect, tol=2)
        ok2 = near(c2, expect)
        ok3 = c3 is not None and not near(c3, expect, tol=2)
        print("阶段1 判定:", "PASS" if ok1 else "FAIL", "(无锁时读真实位, 消息改不了轮询)")
        print("阶段2 判定:", "PASS" if ok2 else "FAIL", "(锁定后读虚拟注入位)")
        print("复原 判定:", "PASS" if ok3 else "FAIL", "(拔锁回真实位)")
    finally:
        try:
            host.unbind()
        except TimeoutError:
            pass
        p.terminate()

    # windows2 全链(DmSoft 层): 自动 dx+mouse_lock
    from imgbot.dmsoft import DmSoft
    p2 = subprocess.Popen([str(exe)])
    time.sleep(1.0)
    hwnd2 = int(u32.FindWindowW("imgbot_kspoll", None) or 0)
    dm = DmSoft()
    try:
        dm.BindWindow(hwnd2, "gdi", "windows2", "windows", 0)
        dm.MoveTo(*PT)
        time.sleep(0.4)
        c4 = wait_read(hwnd2)
        print(f"windows2 链: 靶读={c4} 期望={expect}")
        ok4 = near(c4, expect)
        print("windows2 判定:", "PASS" if ok4 else "FAIL", "(Bind 即锁, MoveTo 落虚拟位)")
    finally:
        dm.UnBindWindow()
        p2.terminate()

    return 0 if ok1 and ok2 and ok3 and ok4 else 3


if __name__ == "__main__":
    sys.exit(main())
