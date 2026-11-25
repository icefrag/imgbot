# -*- coding: utf-8 -*-
"""烟测: 安卓输入常驻会话(ADR-0002)真机验收(雷电9/Android 9 基准).

用法: uv run scripts/adb_session_smoke.py [serial]

验收项(Q7 决策 5, 2026-11-23 修订: adb 传输层单次往返实测 ~40ms 固定停顿,
tap/key 按 scrcpy 同款发后不等派发 —— 验收线改为"派发延迟"):
  1. 派发延迟: session 路 tap 30 次取中位 ≤20ms(shell 路同坐标对照, 基准 ~146ms);
  2. 等价: 下拉通知栏(shell swipe)与收起(session BACK)前后截图按灰差>30 像素计数,
     两路都在同一处产生真实画面响应;
  3. swipe 同步时长安档: 300ms 手势往返 ~340ms(队列排空后测, 排队积压另算);
  4. 断线自愈: 杀驻留件后下一次 tap 自动重建会话并成功;
  5. 生命周期: close() 后设备端驻留件退出。

落点说明: 点击坐标取 (270, 640) —— 540x960 桌面图标区下方空白, 无副作用。
"""
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from imgbot import vision
from imgbot.adb import AdbDevice, list_devices
from imgbot.adb_session import SessionDevice, set_log

N = 30
PROBE_XY = (270, 640)


def changed_pixels(a: np.ndarray, b: np.ndarray) -> int:
    d = np.abs(a.astype(int) - b.astype(int)).max(axis=2)
    return int((d > 30).sum())


def bench_tap(device, n=N, label=""):
    device.tap(*PROBE_XY)  # 预热一发
    ts = []
    for _ in range(n):
        t = time.perf_counter()
        device.tap(*PROBE_XY)
        ts.append((time.perf_counter() - t) * 1000)
    med = statistics.median(ts)
    print(f"[{label}] tap {n} 次: med {med:.1f} / min {min(ts):.1f} / max {max(ts):.1f} ms")
    return med


def helper_pids(shell: AdbDevice) -> list[str]:
    """驻留件进程 pid(app_process 名; 该设备上唯一, 常规系统无裸 app_process 常驻)。
    grep 无匹配 exit=1 → 无残留(adb._run 视为命令失败, 就地吞掉)。"""
    try:
        out = shell.shell("ps -A | grep app_process")
    except Exception:
        return []
    return [l.split()[1] for l in out.splitlines() if l.split()]


def main():
    serial = sys.argv[1] if len(sys.argv) > 1 else list_devices()[0]
    shell = AdbDevice(serial)
    print(f"设备 {serial}: {shell.shell('getprop ro.build.version.release').strip()}")

    warns = []
    set_log(lambda msg, level="warn": warns.append(msg))

    # ---- 1. shell 路基线 ----
    med_shell = bench_tap(shell, label="shell ")

    # ---- 2. session 路: 建立 + 派发延迟 ----
    dev = SessionDevice(serial)
    t0 = time.perf_counter()
    dev.tap(*PROBE_XY)  # 首发含 push+JVM 启动
    print(f"[setup] 会话建立+首发: {(time.perf_counter() - t0) * 1000:.0f} ms")
    med_sess = bench_tap(dev, label="sessns")
    verdict = "PASS" if med_sess <= 20 else "FAIL"
    print(f"[speed] 中位比 shell 快 {med_shell / max(med_sess, 0.001):.0f}x; "
          f"验收线 <=20ms: {verdict}")

    # ---- 3. 等价轮: shell 下拉通知栏 / session BACK 收起 ----
    ref = vision.decode_png(shell.screenshot())
    shell.swipe(270, 30, 270, 700, 300)
    time.sleep(0.8)
    n1 = changed_pixels(ref, vision.decode_png(shell.screenshot()))
    print(f"[equiv] shell swipe 下拉通知栏: 变化像素 {n1} ({'有响应' if n1 > 3000 else '无响应!'})")

    before = vision.decode_png(shell.screenshot())
    dev.keyevent(4)  # BACK 收起通知栏(session 路, 异步派发)
    time.sleep(0.8)  # 顺带排空派发积压
    n2 = changed_pixels(before, vision.decode_png(shell.screenshot()))
    print(f"[equiv] session BACK 收起: 变化像素 {n2} ({'有响应' if n2 > 3000 else '无响应!'})")

    # ---- 4. swipe 同步时长(积压排空后) ----
    t0 = time.perf_counter()
    dev.swipe(270, 30, 270, 700, 300)
    swipe_ms = (time.perf_counter() - t0) * 1000
    print(f"[swipe] 300ms 手势同步往返: {swipe_ms:.0f} ms (档位 ~340ms = 手势+单次往返)")
    time.sleep(0.8)

    dev.keyevent(4)  # 恢复桌面原状
    time.sleep(0.8)

    # ---- 5. 断线自愈: 杀驻留件 → 下一条指令自动重建 ----
    pids = helper_pids(shell)
    print(f"[recover] 驻留件进程: {pids or '未找到'}")
    for pid in pids:
        shell.shell(f"kill {pid}")
    time.sleep(0.5)
    t0 = time.perf_counter()
    dev.tap(*PROBE_XY)                      # 异步: 可能已触发重建, 也可能进死窗
    r = dev._send("ping")                   # 同步 ping: 强制暴露会话状态并计量重建耗时
    rec_ms = (time.perf_counter() - t0) * 1000
    print(f"[recover] 杀驻留件后 tap+ping: {rec_ms:.0f} ms ({r}; 重建成功={not dev._locked_out})")

    # ---- 6. 生命周期 ----
    dev.close()
    time.sleep(1.0)
    left = helper_pids(shell)
    print(f"[lifecycle] close 后驻留件残留: {left or '无(已自退)'}")

    print(f"[log] 诊断回调收到 {len(warns)} 条: {warns[:3]}")
    ok = (med_sess <= 20 and n1 > 3000 and n2 > 3000 and not dev._locked_out and not left)
    print(f"\n结果: {'PASS' if ok else 'FAIL'}(派发 {med_sess:.1f}ms, 等价 {n1}/{n2}px, "
          f"自愈={not dev._locked_out}, 残留 {left})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
