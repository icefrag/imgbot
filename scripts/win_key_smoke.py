# -*- coding: utf-8 -*-
"""windows 消息通道后台按键烟测(真机验收: 推翻或坐实"键盘死案"旧结论).

对指定窗口 key_tap 一组键(默认 enter / F1 / 数字1), 游戏内人工目验反应
(聊天开合/技能快捷位)。发的是带扫描码+状态位的 WM_KEYDOWN/KEYUP, 按大漠
windows 通道语义。原理边界: 消息只达"从消息循环读键"的窗口过程 —— 游戏若走
GetAsyncKeyState/DirectInput 轮询硬件态, 消息送达也无反应, 那是原理边界不是
bug, 换 keypad='dx'(进程内合成 + hook_keystate, 大漠同款解法)。

⚠ 目标以管理员运行时(UIPI)本脚本必须同级(管理员)启动, 否则 PostMessage 直接
被系统拒绝并抛 UipiError —— 那是权限墙, 不是"游戏无视消息", 别记错结论。
用法: 管理员终端  uv run scripts/win_key_smoke.py [标题关键字] [键名...]
      键名: enter/F1/1/esc/space 或 0x.. 整数 VK
"""
import sys
import time

VK_CODES = {"enter": 0x0D, "f1": 0x70, "1": 0x31, "esc": 0x1B, "space": 0x20}


def main():
    title = sys.argv[1] if len(sys.argv) > 1 else "华夏免费版"
    from imgbot.dmsoft import DmSoft
    from imgbot.win32.window import GameWindow

    win = GameWindow.find(title_keyword=title)
    if win is None:
        print(f"没找到标题含「{title}」的可见窗口")
        return 1
    print(f"目标: hwnd={win.hwnd:#x}")
    dm = DmSoft()
    dm.BindWindow(win.hwnd, "gdi", "windows", "windows", 0)   # 全 windows 消息通道
    keys = sys.argv[2:] if len(sys.argv) > 2 else ["enter", "F1", "1"]
    print(f"3 秒后开始按键: {keys} —— 请盯着游戏画面看反应 (Ctrl+C 中止)")
    time.sleep(3)
    for k in keys:
        vk = VK_CODES.get(k.lower(), int(k, 0))   # 识别名或 0x.. 整数 VK
        dm.KeyPress(vk)
        print(f"  KeyPress {k} (VK 0x{vk:X}) 已投递")
        time.sleep(1.5)
    dm.UnBindWindow()
    print("完成: 游戏内有反应=消息通道可用; 没反应=脚本头'原理边界'(换 dx), "
          "把目验结论记回 AGENTS.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
