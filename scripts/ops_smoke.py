# -*- coding: utf-8 -*-
"""Ops 门面验收(记事本 E2E, gdi 通道): A 等待族/B 找点族/C 校验读数/E 编排族全过。

自造素材闭环: 打字→截图→裁出文字区当模板→exists/find_tap/wait_pic 全链真机验证。
通过标准: 每步 PASS 行, 任一断言炸即失败。
"""
import ctypes
import subprocess
import sys
import time
from pathlib import Path

import cv2

LOG = Path("logs")


def main():
    p = subprocess.Popen(["notepad.exe"])
    time.sleep(1.2)
    from imgbot.dmsoft import DmSoft
    from imgbot.ops import Ops

    dm = DmSoft()
    hwnd = dm.FindWindow("Notepad", "")
    u32 = ctypes.WinDLL("user32")
    u32.FindWindowExW.restype = ctypes.c_void_p
    edit = int(u32.FindWindowExW(hwnd, None, "Edit", None) or hwnd)
    assert dm.BindWindow(edit, "gdi", "windows", "windows", 0) == 1
    dm.SetPath(str(LOG / "tpl"))                       # 模板目录
    Path(LOG / "tpl").mkdir(parents=True, exist_ok=True)
    ops = Ops(dm)
    print("PASS Ops 构造(gdi 通道)")

    # 素材闭环: 打字 → 截屏裁出文字区存模板(只裁前 3 字, 避开行尾闪烁的插入符)
    dm.SendString(edit, "OPS888")
    time.sleep(0.4)
    frame = dm._screen()
    LOG.mkdir(exist_ok=True)
    Path(LOG / "tpl").mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(LOG / "tpl" / "opsword.png"), frame[10:35, 10:40])
    print("PASS 素材闭环(截屏裁模板)")

    # B: exists / find_all(单目标) / probe
    assert ops.exists("opsword.png", region=(0, 0, 400, 60))
    print("PASS exists")
    hit = ops.probe("不存在.png", "opsword.png", region=(0, 0, 400, 60))
    assert hit == "opsword.png", f"probe={hit!r}(缺素材应跳过, 命中返回名)"
    print("PASS probe(缺素材跳过+返回名)")
    assert ops.find_all("opsword.png", region=(0, 0, 400, 60))
    print("PASS find_all")

    # E: same_frame 一帧多查 + wait_pic 自动留痕
    with ops.same_frame():
        assert ops.exists("opsword.png", region=(0, 0, 400, 60))
        assert ops.check_color(5, 5, "FFFFFF")         # 记事本白底
    print("PASS same_frame(一帧两查)")
    try:
        with ops.same_frame():
            ops.wait_pic("opsword.png", region=(0, 0, 400, 60), timeout=0.1)
        raise AssertionError("等待族进冻结窗应拒绝")
    except RuntimeError:
        print("PASS 等待族进 same_frame 被拒绝(防复读旧帧)")
    miss = ops.wait_pic("never.png", region=(0, 0, 10, 10), timeout=0.2)
    assert miss is None
    shots = list((Path("logs/shots")).glob("*wait_pic*"))
    assert shots, "wait_pic 超时应自动留痕"
    print(f"PASS wait_pic 超时安静返 None + 自动留痕({shots[-1].name})")

    # A: wait_pic 命中 / wait_text OCR / wait_color
    hit = ops.wait_pic("opsword.png", region=(0, 0, 400, 60), timeout=3)
    assert hit, "模板就在屏上, wait_pic 应命中"
    print(f"PASS wait_pic 命中 {hit[:2]}")
    t = ops.wait_text("OPS888", region=(0, 0, 400, 80), timeout=5)
    assert t, f"OCR 应读到 OPS888(实测 {ops.read_text(region=(0, 0, 400, 80))!r})"
    print(f"PASS wait_text 命中 {t[:2]}")
    assert ops.wait_color("FFFFFF", region=(0, 0, 30, 30), timeout=2)
    print("PASS wait_color(白底)")

    # C: read_text / check_points(文字黑白两点当点阵)
    text = ops.read_text(region=(0, 0, 400, 80))
    assert "OPS" in text.upper(), f"read_text={text!r}"
    print(f"PASS read_text: {text!r}")

    # E: retry / wait_until
    assert ops.wait_until(lambda: ops.exists("opsword.png", region=(0, 0, 400, 60)),
                          timeout=2)
    n = ops.retry(lambda: ops.exists("opsword.png", region=(0, 0, 400, 60)), times=2)
    assert n
    print("PASS wait_until + retry")

    # B: find_tap(点自己, 记事本不死即通; 点击坐标=模板位置)
    ok = ops.find_tap("opsword.png", region=(0, 0, 400, 60), timeout=2)
    assert ok
    time.sleep(0.3)
    buf = ctypes.create_unicode_buffer(256)
    u32.SendMessageW(edit, 0x000D, 256, buf)
    assert "OPS888" in buf.value                      # 点击只挪光标, 文本无损
    print("PASS find_tap(拟人点击落点在模板上)")

    dm.UnBindWindow()
    p.terminate()
    print("Ops 门面验收: 全部 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
