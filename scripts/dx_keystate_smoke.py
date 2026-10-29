# -*- coding: utf-8 -*-
"""键态钩子验收(GetAsyncKeyState 轮询型游戏输入): 靶 kspoll32(不看消息, 每帧轮询)。

两阶段:
  阶段1 不挂钩: 打一轮 KEYDOWN/UP —— kd/char 涨(消息配方+手动 TranslateMessage 通),
    down=0(坐实死案: 轮询型输入对纯消息注入免疫);
  阶段2 挂钩: 同样一轮 —— down/latch/gks 涨(虚拟状态被轮询读到, 读后清语义成立)。
通过标准: 阶段1 down==0 且 kd>=1 且 char>=1; 阶段2 down>=1 且 latch==1 且 gks>=1。
"""
import ctypes
import re
import subprocess
import sys
import time
from pathlib import Path

from imgbot.dx import DxHost
from imgbot.dx.host import KEY_DOWN, KEY_UP

u32 = ctypes.WinDLL("user32")
VK_A = 0x41


def read_counters(hwnd: int) -> dict:
    buf = ctypes.create_unicode_buffer(128)
    u32.GetWindowTextW(hwnd, buf, 128)
    m = re.match(r"down=(\d+) latch=(\d+) gks=(\d+) kd=(\d+) char=(\d+)", buf.value)
    return {k: int(v) for k, v in zip(("down", "latch", "gks", "kd", "char"),
                                      m.groups())} if m else {}


def wait_counters(hwnd: int, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        c = read_counters(hwnd)
        if c:
            return c
        time.sleep(0.05)
    return {}


def main():
    exe = Path(__file__).resolve().parent.parent / "imgbot" / "dx" / "bin" / "kspoll32.exe"
    p = subprocess.Popen([str(exe)])
    time.sleep(1.0)
    u32.FindWindowW.restype = ctypes.c_void_p
    hwnd = int(u32.FindWindowW("imgbot_kspoll", None) or 0)
    if not hwnd:
        print("kspoll 窗口没找到")
        return 1
    print(f"kspoll pid={p.pid} hwnd=0x{hwnd:X}")

    host = DxHost(p.pid)
    host.bind()
    try:
        host.bind_input(hwnd)
        # -- 阶段1: 无钩子, 纯消息(预期: kd/char 通, down 仍为 0) --
        host.key(VK_A, KEY_DOWN)
        time.sleep(0.3)
        host.key(VK_A, KEY_UP)
        c1 = wait_counters(hwnd)
        print(f"阶段1(无钩): {c1}")
        # -- 阶段2: 挂键态钩子, 再打一轮 --
        host.hook_keystate()
        host.key(VK_A, KEY_DOWN)
        time.sleep(0.3)
        host.key(VK_A, KEY_UP)
        time.sleep(0.3)
        c2 = wait_counters(hwnd)
        print(f"阶段2(挂钩): {c2}")

        ok1 = c1.get("down") == 0 and c1.get("kd", 0) >= 1 and c1.get("char", 0) >= 1
        grew = lambda k: c2.get(k, -1) > c1.get(k, 0)
        ok2 = grew("down") and c2.get("latch", 0) - c1.get("latch", 0) == 1 \
            and grew("gks") and grew("kd") and grew("char")
        print("阶段1 判定:", "PASS" if ok1 else "FAIL",
              "(kd/char=消息+Translate 通, down=0 死案坐实)")
        print("阶段2 判定:", "PASS" if ok2 else "FAIL",
              "(down/gks=虚拟状态被轮询读到, latch=读后清)")
        return 0 if ok1 and ok2 else 3
    finally:
        host.unbind()
        p.terminate()


if __name__ == "__main__":
    sys.exit(main())
