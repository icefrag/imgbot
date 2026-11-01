# -*- coding: utf-8 -*-
"""构建 dx 注入件(需要 zig, 默认 D:/tools/zig/zig.exe): x86+x64 双份.

用法: uv run dx/native/build.py [--zig 路径]
产物: dx/bin/{dmcore32,dmcore64}.dll + {injector32,injector64}.exe
"""
import subprocess
import sys
from pathlib import Path

NATIVE = Path(__file__).parent
BIN = NATIVE.parent / "bin"


def build(zig: str):
    BIN.mkdir(exist_ok=True)
    targets = [("x86-windows", "32"), ("x86_64-windows", "64")]
    for target, tag in targets:
        for src, kind in (("dmcore.c", "dll"), ("injector.c", "exe")):
            out = BIN / f"{src.split('.')[0]}{tag}.{kind}"
            cmd = [zig, "cc", "-target", target, "-O2", "-s"]
            if kind == "dll":
                cmd += ["-shared"]
            cmd += ["-o", str(out), str(NATIVE / src)]
            print("+", " ".join(cmd))
            subprocess.run(cmd, check=True)
    for junk in BIN.glob("*.pdb"):
        junk.unlink()
    for junk in BIN.glob("*.lib"):
        junk.unlink()
    # d3d9 验收靶(x86 only, 测试用)
    cmd = [zig, "cc", "-target", "x86-windows", "-O2", "-s",
           "-o", str(BIN / "d3d9test32.exe"), str(NATIVE / "d3d9test.c"),
           "-luser32", "-lgdi32"]
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)
    # 键态钩子验收靶(x86 only, 测试用)
    cmd = [zig, "cc", "-target", "x86-windows", "-O2", "-s",
           "-o", str(BIN / "kspoll32.exe"), str(NATIVE / "kspoll.c"),
           "-luser32"]
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)
    # 注入判别探针(x86 only, 诊断用: 远程 LoadLibrary 后查模块落地)
    cmd = [zig, "cc", "-target", "x86-windows", "-O2", "-s",
           "-o", str(BIN / "inject_probe32.exe"), str(NATIVE / "inject_probe.c")]
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)
    print("done ->", BIN)


if __name__ == "__main__":
    z = "D:/tools/zig/zig.exe"
    if "--zig" in sys.argv:
        z = sys.argv[sys.argv.index("--zig") + 1]
    build(z)
