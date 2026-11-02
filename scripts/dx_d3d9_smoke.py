# -*- coding: utf-8 -*-
"""阶段3a 验收: d3d9 vtable 钩子闭环。

靶子 d3d9test32.exe(6 秒后建设备, 画面颜色循环)。流程:
注入 → hook_d3d9 → 等设备建立/帧流 → 取两帧验帧差(变色循环 → 必 > 0)
→ 秒帧率(frame_seq 差) → 遮挡测试(把窗口挪到屏幕外缘再取帧, 仍应新鲜)。
通过标准: 帧差 > 5 且遮挡后 frame_seq 仍在涨。
"""
import subprocess
import sys
import time
from pathlib import Path

import cv2

from imgbot.dx import DxHost, mean_diff
from imgbot.win32.window import GameWindow

LOG = Path("logs")
BIN = Path(__file__).parent.parent / "imgbot" / "dx" / "bin"


def main():
    app = subprocess.Popen([str(BIN / "d3d9test32.exe")])
    try:
        win = None
        for _ in range(30):
            win = GameWindow.find(class_name="imgbot_d3d9test")
            if win:
                break
            time.sleep(0.2)
        if not win:
            print("测试靶窗口没出现")
            return 1
        print(f"靶子: pid={app.pid} hwnd=0x{win.hwnd:X}")
        host = DxHost(app.pid)
        host.bind()
        try:
            host.hook_d3d9()
            print("d3d9 钩子: OK(Direct3DCreate9 hotpatch)")
            # 靶子 6 秒后才建设备, 等帧流出现
            deadline = time.time() + 15
            while time.time() < deadline:
                seq, w, h, fmt = host.frame_info()
                if seq:
                    break
                time.sleep(0.3)
            else:
                print("没有帧流(设备建立没被钩到? 检查注入时机)")
                return 3
            f1 = host.capture(fresh_wait=1.5)
            time.sleep(0.7)
            f2 = host.capture(fresh_wait=1.5)
            d = mean_diff(f1, f2)
            seq1 = host.frame_info()[0]
            time.sleep(1.0)
            fps = host.frame_info()[0] - seq1
            print(f"取帧: {f1.shape[1]}x{f1.shape[0]} 帧差={d:.1f} "
                  f"({'>5 变色循环被捕获' if d > 5 else '画面没变?'})")
            print(f"帧率: ~{fps} fps(swap_calls={host.swap_calls}, err={host.last_err:#06x})")
            LOG.mkdir(exist_ok=True)
            cv2.imwrite(str(LOG / "dx_d3d9_frame.png"), f2)

            # 遮挡测试: 窗口挪到屏幕底部边缘外一半(仍渲染), 帧序号应继续涨
            import ctypes
            u32 = ctypes.WinDLL("user32")
            cy = u32.GetSystemMetrics(1)      # SM_CYSCREEN
            # 半截留在屏内: 完全离屏会触发遮挡节流(ADR-0002), 帧序号停涨造成假 FAIL
            u32.SetWindowPos(win.hwnd, None, 100, max(cy - 240, 0), 0, 0,
                             0x0001 | 0x0004)
            time.sleep(1.0)
            s0 = host.frame_info()[0]
            time.sleep(1.0)
            s1 = host.frame_info()[0]
            f3 = host.capture(fresh_wait=1.5)
            print(f"遮挡测试: 半屏外 frame_seq {s0} -> {s1} "
                  f"({'仍在渲染, 后台可截' if s1 > s0 else '停渲染了!'})")

            # 重绑续捕(BYE 后 detached 闸住 our_present, 重绑 hook_d3d9 清闸无缝续捕):
            # 同一进程 unbind -> bind -> hook_d3d9 -> 必须再见到新帧流(修复前假成功死局)
            host.unbind()
            host.bind()
            host.hook_d3d9()
            seq2 = 0
            t0 = time.time()
            while time.time() - t0 < 10:
                seq2 = host.frame_info()[0]
                if seq2:
                    break
                time.sleep(0.3)
            f4 = host.capture(fresh_wait=1.5)
            d2 = mean_diff(f3, f4)
            ok_rebind = seq2 > 0 and d2 > 5
            print(f"重绑续捕: frame_seq={seq2} 帧差={d2:.1f} "
                  f"({'续捕正常' if ok_rebind else '重绑后死帧!'})")
            ok = d > 5 and s1 > s0 and ok_rebind
            print("d3d9 验收:", "PASS" if ok else "FAIL")
            return 0 if ok else 4
        finally:
            try:
                host.unbind()
            except TimeoutError:
                pass
    finally:
        app.terminate()


if __name__ == "__main__":
    sys.exit(main())
