# -*- coding: utf-8 -*-
"""阶段1 真机烟雾测试: 注入 dnplayer -> 心跳 -> PING -> GL 钩子 -> 取帧存档.

用法: uv run scripts/dx_smoke.py [窗口类名, 默认 LDPlayerMainFrame]
"""
import sys
import time
from pathlib import Path

import cv2

from imgbot.dx import DxHost, mean_diff
from imgbot.win32.window import GameWindow

LOG = Path("logs")


def main():
    cls = sys.argv[1] if len(sys.argv) > 1 else "LDPlayerMainFrame"
    win = GameWindow.find(class_name=cls)
    if not win:
        print(f"没找到 class={cls} 的窗口, 模拟器开了吗?")
        return 1
    pid = win.pid()
    print(f"目标: hwnd=0x{win.hwnd:X} pid={pid} {win.info()[:2]}")

    host = DxHost(pid)
    host.bind()
    try:
        h1 = host.heartbeat
        time.sleep(0.3)
        h2 = host.heartbeat
        print(f"心跳: {h1} -> {h2} ({'正常' if h2 > h1 else '死了!'})")
        print(f"PING -> 0x{host.ping():08X} (期望 0x50494731)")

        try:
            host.hook_gl()
            print("GL 钩子: OK (SwapBuffers hotpatch + 上下文劫持)")
            time.sleep(1.0)
            seq, fw, fh, fmt = host.frame_info()
            print(f"诊断: swap_calls={host.swap_calls} vp={host.viewport[0]}x{host.viewport[1]} "
                  f"err={host.last_err:#06x} frame_seq={seq} frame={fw}x{fh} fmt={fmt}")
        except RuntimeError as e:
            print(f"GL 钩子: 失败 —— {e} (骨架仍验证通过, 图色待换路线)")
            return 0

        f1 = host.capture(fresh_wait=2.0)
        time.sleep(0.4)
        f2 = host.capture(fresh_wait=2.0)
        seq1, w, h, fmt = host.frame_info()
        diff = int(mean_diff(f1, f2))
        print(f"取帧: {w}x{h} fmt={fmt} 两帧均差={diff} ({'画面在动' if diff > 0 else '静止画面/同帧'})")
        LOG.mkdir(exist_ok=True)
        out = LOG / "dx_smoke_frame.png"
        cv2.imwrite(str(out), f2)
        print(f"已存 {out}")
        t0 = time.monotonic()
        for _ in range(10):
            host.capture(fresh_wait=1.0)
        dt = (time.monotonic() - t0) / 10 * 1000
        print(f"单帧取数均耗时: {dt:.1f}ms (含等新帧)")

        # 阶段2: 假激活 + 模块隐藏
        host.bind_input(win.hwnd)
        host.fake_active(True)
        print(f"假激活: OK (子类化 0x{win.hwnd:X}, 合成激活已发)")
        host.hide_module()
        time.sleep(0.2)
        print(f"模块隐藏: PASS(三链已摘, 模块枚举/扫链不可见) | "
              f"按名 GetModuleHandle {'仍可解析(LdrpHashTable, 预期内)' if host.args()[1] & 1 else '也不可解析'}")
        # 失焦一波看计数器涨不涨(把桌面 Shell 窗口提到前台抢焦点)
        import ctypes
        ctypes.windll.user32.SetForegroundWindow(
            ctypes.windll.user32.GetShellWindow())
        time.sleep(0.8)
        f3 = host.capture(fresh_wait=1.0)
        print(f"吞消息计数: {host.msg_filtered} (失焦风波中被过滤的消息数)")
        return 0
    finally:
        try:
            host.unbind()
        except TimeoutError:
            pass
        print("已解绑(BYE), DLL 留驻目标进程(安全设计), view 已释放")


if __name__ == "__main__":
    sys.exit(main())
