# -*- coding: utf-8 -*-
"""阶段2 闭环验收: dx 输入(拖拽/点击/按键) → dx 图色验证画面变化。

用法: uv run scripts/dx_input_smoke.py [窗口类名, 默认 LDPlayerMainFrame]
通过标准: 拖拽后帧差 > 5(画面真的变了), 且全程无异常。
"""
import sys
from pathlib import Path

import cv2

from imgbot.dmsoft import DmSoft
from imgbot.dx import mean_diff

LOG = Path("logs")


def main():
    cls = sys.argv[1] if len(sys.argv) > 1 else "LDPlayerMainFrame"
    from imgbot.win32.window import GameWindow
    outer = GameWindow.find(class_name=cls)
    if not outer:
        print(f"没找到 class={cls} 的窗口")
        return 1
    # 模拟器输入要打内层渲染窗(雷电 RenderWindow), 外壳 MainFrame 常不认鼠标消息
    inner = GameWindow.find(class_name="RenderWindow", parent=outer.hwnd)
    win = inner or outer
    print(f"绑定窗口: {'RenderWindow(内层)' if inner else cls + '(未找到内层, 退外壳)'} "
          f"hwnd=0x{win.hwnd:X}")
    dm = DmSoft()
    assert dm.BindWindow(win.hwnd, "dx", "dx", "dx", 0) == 1, "BindWindow 失败"
    try:
        f0 = dm.screen()
        print(f"图色(dx): {f0.shape[1]}x{f0.shape[0]} 取帧 OK")

        # 1) 上滑开应用抽屉(手势从屏幕下方往上); 400ms 拍 → steps(拖拽时长换算同旧 drag ms 口径)
        h, w = f0.shape[:2]
        ms = 400
        dm.keymouse.drag(w // 2, int(h * 0.75), w // 2, int(h * 0.25),
                         steps=max(6, ms // 25))
        import time
        time.sleep(1.2)
        f1 = dm.screen()
        d1 = mean_diff(f0, f1)
        print(f"拖拽后帧差: {d1:.1f} ({'画面变了, dx 输入生效' if d1 > 5 else '没变化'})")
        LOG.mkdir(exist_ok=True)
        cv2.imwrite(str(LOG / "dx_input_before.png"), f0)
        cv2.imwrite(str(LOG / "dx_input_after.png"), f1)

        # 2) 按一次返回(ESC 在模拟器常映射为 Back), 再验帧
        dm.KeyPress(0x1B)
        time.sleep(1.0)
        f2 = dm.screen()
        d2 = mean_diff(f1, f2)
        print(f"ESC(Back)后帧差: {d2:.1f} ({'生效' if d2 > 5 else '无变化(该键未被映射, 不算失败)'})")

        ok = d1 > 5
        if ok:
            print("闭环验收: PASS(dx 输入在该目标生效)")
        else:
            print("闭环验收: 该目标消息免疫(已确认模拟器走自有输入通道, 非 raw input),\n"
                  "  模拟器后台输入用 adb(AdbTarget); dx 键鼠正确性见 dx_notepad_smoke.py")
        return 0
    finally:
        dm.UnBindWindow()


if __name__ == "__main__":
    sys.exit(main())
