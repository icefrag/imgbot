# -*- coding: utf-8 -*-
"""华夏免费版(复古无锻造私服)后台键鼠真机验收烟测.

三重墙与解法(实机结论 2026-10-31, 详见 AGENTS.md):
1. [盾]登录器给 game.exe 挂进程 DACL 拒注入权 → OWNER WRITE_DAC 改写成 Everyone
   全权解封(本脚本自动做, 幂等);
2. 游戏提权运行(HIGH IL) → 本脚本必须提权跑(普通权限 PostMessage 全 err5);
3. "鼠标能移动不能点击" → 根因=点击处理有窗口激活门槛 → mouse="dx" +
   dm.EnableFakeActive(1) 假激活(2026-11-01 世界画面实测 8/8 走路; 光标锁/键态钩均不需要).

验收判读全自动: 前后截图 diff(W 键=技能窗开, 大 diff; 地面点击=人物走路,
持续 diff)。用法: 管理员终端 uv run scripts/huaxia_smoke.py
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time

import cv2
import numpy as np

CLS = "*GodClass*"
VK_W = 0x57
GROUND = (400, 260)          # 画面中部空地(人物会走过去)


def unlock_dacl(pid: int) -> None:
    """OWNER WRITE_DAC → Everyone 全权([盾]挂的锁, 无看门狗; 已解封则幂等跳过)."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    h = ctypes.c_void_p(k32.OpenProcess(0x40000, False, pid))  # WRITE_DAC
    if not h:
        print(f"  WRITE_DAC 拿不到(err={ctypes.get_last_error()}), 若注入仍通可无视")
        return
    sid = ctypes.c_void_p()
    adv.ConvertStringSidToSidW("S-1-1-0", ctypes.byref(sid))

    class ACL(ctypes.Structure):
        _fields_ = [("rev", ctypes.c_ubyte), ("sbz1", ctypes.c_ubyte),
                    ("size", ctypes.c_ushort), ("aces", ctypes.c_ushort),
                    ("sbz2", ctypes.c_ushort)]
    buf = ctypes.create_string_buffer(64)
    pacl = ctypes.cast(buf, ctypes.POINTER(ACL))
    adv.InitializeAcl(pacl, 64, 2)
    adv.AddAccessAllowedAce(pacl, 2, 0x1F0FFF, sid)
    rc = adv.SetSecurityInfo(h, 6, 0x80000004, None, None,
                             ctypes.cast(pacl, ctypes.c_void_p), None)
    k32.CloseHandle(h)
    print(f"  DACL 解封 rc={rc}(0=成功)")


def px_changed(a, b):
    """判读尺 = 变化像素数(>30 灰差), 不用均值 —— 大静态背景会把均值稀释到
    2-3, 局部大变化(面板开合 17k px)会被误判为无反应(华夏实测踩过)。"""
    return int((np.abs(b - a).max(axis=2) > 30).sum())


def diff3(a, b, c):
    return [px_changed(x, y) for x, y in ((a, b), (b, c), (a, c))]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # GBK 控制台跑 emoji 不炸
    if not ctypes.WinDLL("shell32").IsUserAnAdmin():
        print("⚠ 必须管理员运行(游戏 HIGH IL, 普通权限消息全被 UIPI 拒)")
        return 1
    from imgbot.dmsoft import DmSoft
    from imgbot.win32.window import GameWindow
    from imgbot.win32.capture import capture_client

    win = GameWindow.find(class_name=CLS)
    if win is None:
        print("没找到华夏窗口(*GodClass*)")
        return 1
    print(f"目标 hwnd={win.hwnd:#x} pid={win.pid()}")
    unlock_dacl(win.pid())

    dm = DmSoft()
    dm.BindWindow(win.hwnd, "gdi", "dx", "windows", 0)
    dm.EnableFakeActive(1)          # 华夏定论配方: dx 消息鼠标+假激活(2026-11-01 实测)
    print("绑定: display=gdi mouse=dx keypad=windows + EnableFakeActive")

    def cap():
        return capture_client(win).astype(int)

    try:
        # 静息噪声
        time.sleep(1.0)
        na = cap(); time.sleep(1.0); nb = cap()
        noise = px_changed(na, nb)
        print(f"静息噪声基线 变化像素={noise}")

        # 1) 键盘: W 开技能窗(用户口径)
        a = cap(); dm.KeyPress(VK_W); time.sleep(1.2); b = cap()
        kw = px_changed(a, b)
        print(f"W 键 变化像素={kw} → {'✅ 技能窗开了' if kw > 3000 else '❌ 无反应'}")
        if kw > 3000:                     # 关回去
            dm.KeyPress(VK_W); time.sleep(0.8)
        cv2.imwrite(r"logs\huaxia_w.png", b.astype(np.uint8))

        # 2) 鼠标: windows2 地面点击 → 人物走过去(持续+持久双大)
        time.sleep(0.8)
        a = cap()
        dm.MoveTo(*GROUND); time.sleep(0.3)
        dm.LeftClick()
        time.sleep(1.2); b = cap()
        time.sleep(1.5); c = cap()
        d = diff3(a, b, c)
        walked = d[0] > 20000 or d[2] > 20000     # 瞬时/持久任一大 = 镜头位移(到得早就无持续变化)
        print(f"地面点击 变化像素(即时/持续/持久)={d} → "
              f"{'✅ 人物走了' if walked else '❌ 点击无效'}")
        cv2.imwrite(r"logs\huaxia_walk.png", c.astype(np.uint8))
        ok = kw > 3000 and walked
        print("=" * 40)
        print("验收结论:", "✅ 全通" if ok else "❌ 未通 — 截图在 logs/, 按 AGENTS.md 决策树换配方")
        return 0 if ok else 2
    finally:
        dm.UnBindWindow()


if __name__ == "__main__":
    sys.exit(main())
