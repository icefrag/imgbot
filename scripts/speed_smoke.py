# -*- coding: utf-8 -*-
"""变速齿轮烟测(大漠 HackSpeed 1:1 复刻): sptest{32,64} 靶上验证 CMD_SPEED 全链
—— 注入 → MinHook inline 钩三钟源 → 倍率生效 → 换档 → 复位。靶子以 QPC/
timeGetTime 各烧一段虚拟时间, 用不被钩的墙钟结算真实耗时, rate=r 时应 ≈
名义值/r(带宽容忍计时粒度)。倍率×10 口径(大漠 0x863: 30=3x, 10=常速)。

用法: uv run scripts/speed_smoke.py [--only 32|64]
"""
import subprocess
import sys
import time
from pathlib import Path

from imgbot.dx.host import DxHost

BIN = Path(__file__).parent.parent / "imgbot" / "dx" / "bin"


def _unbind(host, p) -> None:
    """靶子可能已自行退出(变速下几秒跑完), BYE 无人应答不算失败。"""
    try:
        host.unbind()
    except TimeoutError:
        if p.poll() is None:
            raise


def run_case(tag: str, rate1: int, rate2: int | None,
             expect_ms: tuple[int, int]) -> bool:
    """spawn 靶 → 注入 → 设 rate1(可选换档 rate2, 均 ×10 口径) → 断言两段墙钟。"""
    exe = BIN / f"sptest{tag}.exe"
    p = subprocess.Popen([str(exe)], stdout=subprocess.PIPE, text=True,
                         creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        assert p.stdout.readline().strip() == "SPTEST ready"
        host = DxHost(p.pid)
        host.bind()
        host.hack_speed(rate1)
        if rate2 is not None:
            time.sleep(0.5)                      # phase0 已检出加速, 再换档
            host.hack_speed(rate2)
            rate = rate2 / 10.0
        else:
            rate = rate1 / 10.0
        results = {}
        deadline = time.monotonic() + 30
        while len(results) < 2 and time.monotonic() < deadline:
            line = p.stdout.readline().strip()
            if not line and p.poll() is not None:
                break
            if line.startswith("SPRESULT"):
                k, v = line.split()[1].split("=")
                results[k] = int(v)
            elif line.startswith("SPTEST ratio"):
                print(f"  [sptest{tag}] {line}")
        _unbind(host, p)
        lo, hi = expect_ms
        ok = True
        for k, nominal in (("qpc_wall_ms", 3000), ("tgt_wall_ms", 2000)):
            got = results.get(k, -1)
            exp = nominal / rate
            passed = lo <= got <= hi
            print(f"  [sptest{tag}] {k}={got}ms 期望≈{exp:.0f}ms(带宽{lo}~{hi})"
                  f" {'✅' if passed else '❌'}")
            ok &= passed
        return ok
    finally:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


def run_restore_case(tag: str) -> bool:
    """设速后立刻复位(×10 口径 10=常速): 靶子应等满 10s 检不出加速, 两段按
    原速跑满 —— 证复位(钩常驻, rate 归 1.0, dm 解绑同款语义)。"""
    exe = BIN / f"sptest{tag}.exe"
    p = subprocess.Popen([str(exe)], stdout=subprocess.PIPE, text=True,
                         creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        assert p.stdout.readline().strip() == "SPTEST ready"
        host = DxHost(p.pid)
        host.bind()
        host.hack_speed(30)
        host.hack_speed(10)                      # rate 归 1.0 常速(时间轴无缝)
        results = {}
        deadline = time.monotonic() + 60
        while len(results) < 2 and time.monotonic() < deadline:
            line = p.stdout.readline().strip()
            if not line and p.poll() is not None:
                break
            if line.startswith("SPRESULT"):
                k, v = line.split()[1].split("=")
                results[k] = int(v)
        _unbind(host, p)
        ok = results.get("qpc_wall_ms", 0) > 2000 and results.get("tgt_wall_ms", 0) > 1400
        print(f"  [sptest{tag}] 恢复后 qpc={results.get('qpc_wall_ms')}ms "
              f"tgt={results.get('tgt_wall_ms')}ms(应≈3000/2000 未加速) "
              f"{'✅' if ok else '❌'}")
        return ok
    finally:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


def main() -> int:
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    tags = [only] if only else ["64", "32"]
    ok = True
    for tag in tags:
        print(f"== sptest{tag}: rate=3(传 30) ==")
        # 3000/3=1000ms, 2000/3≈667ms; 带宽 500~1600 同时覆盖两段(粒度/起跑余量)
        ok &= run_case(tag, 30, None, (500, 1600))
        print(f"== sptest{tag}: rate=2 → 换档 rate=4(传 20→40) ==")
        # 换档在 phase0 检出后落地, 段耗 ≈ 名义/4; 带宽校验换档真的生效(若仍 2x → 1500ms 出带)
        ok &= run_case(tag, 20, 40, (450, 1100))
        print(f"== sptest{tag}: 设速→恢复 ==")
        ok &= run_restore_case(tag)
    print("烟测结果:", "全部通过 ✅" if ok else "存在失败 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
