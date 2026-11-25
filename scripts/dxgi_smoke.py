# -*- coding: utf-8 -*-
"""DXGI 图色验收(工单 damo-bg-capture/01): d3d11 vtable 钩闭环, x86+x64 双靶。

靶子 d3d11test{32,64}.exe(6 秒后建 D3D11Device+SwapChain, 画面颜色循环)。流程:
注入 → hook_dxgi → 等帧流 → 取两帧验帧差(变色循环 → 必 > 5)→ 秒帧率
→ 重绑续捕(unbind→bind→hook_dxgi 后必须再见新帧流)。
通过标准: 帧差 > 5 且重绑后 frame_seq 继续涨。
用法: uv run scripts/dxgi_smoke.py [64]   (无参=32, "64"=64 位靶)
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
    tag = sys.argv[1] if len(sys.argv) > 1 else "32"
    app = subprocess.Popen([str(BIN / f"d3d11test{tag}.exe")])
    try:
        win = None
        for _ in range(30):
            win = GameWindow.find(class_name="imgbot_d3d11test")
            if win:
                break
            time.sleep(0.2)
        if not win:
            print("测试靶窗口没出现")
            return 1
        print(f"靶子({tag}位): pid={app.pid} hwnd=0x{win.hwnd:X}")
        host = DxHost(app.pid)
        host.bind()
        try:
            host.hook_dxgi()
            print("DXGI 钩子: OK(创建入口 MinHook + swap Present 槽)")
            # 靶子 6 秒后才建 swap, 等帧流出现
            deadline = time.time() + 15
            while time.time() < deadline:
                seq, w, h, fmt = host.frame_info()
                if seq:
                    break
                time.sleep(0.3)
            else:
                print("没有帧流(swap 创建没被钩到? 检查注入时机/hook_auto 路由)")
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
            cv2.imwrite(str(LOG / f"dx_d3d11{tag}_frame.png"), f2)

            # 重绑续捕(BYE 后 detached 闸住 our_dxgi_present, 重绑 hook_dxgi 清闸无缝续捕)
            host.unbind()
            host.bind()
            host.hook_dxgi()
            seq2 = 0
            t0 = time.time()
            while time.time() - t0 < 10:
                seq2 = host.frame_info()[0]
                if seq2:
                    break
                time.sleep(0.3)
            f4 = host.capture(fresh_wait=1.5)
            d2 = mean_diff(f2, f4)
            ok_rebind = seq2 > 0 and d2 > 5
            print(f"重绑续捕: frame_seq={seq2} 帧差={d2:.1f} "
                  f"({'续捕正常' if ok_rebind else '重绑后死帧!'})")
            ok = d > 5 and ok_rebind
            print(f"d3d11{tag} 验收:", "PASS" if ok else "FAIL")
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
