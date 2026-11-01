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

# 遮蔽串清单: (C 标识符后缀, 字面量) —— dmcore.c 用 OBF_<后缀> 访问解码后的串。
# 覆盖面 = 全部 GetProcAddress/GetModuleHandleA 字面量 + 共享内存/互斥体名格式。
OBF_STRS = [
    ("USER32", "user32.dll"), ("GDI32", "gdi32.dll"),
    ("OPENGL32", "opengl32.dll"), ("D3D9", "d3d9.dll"),
    ("SWAPBUFFERS", "SwapBuffers"), ("WGLSWAPBUFFERS", "wglSwapBuffers"),
    ("DIRECT3DCREATE9", "Direct3DCreate9"),
    ("GLREADPIXELS", "glReadPixels"), ("GLGETINTEGERV", "glGetIntegerv"),
    ("GLGETERROR", "glGetError"), ("WGLCREATECONTEXT", "wglCreateContext"),
    ("WGLMAKECURRENT", "wglMakeCurrent"), ("WGLGETCURRENTCONTEXT", "wglGetCurrentContext"),
    ("WGLGETCURRENTDC", "wglGetCurrentDC"),
    ("GETKEYSTATE", "GetKeyState"), ("GETASYNCKEYSTATE", "GetAsyncKeyState"),
    ("GETCURSORPOS", "GetCursorPos"), ("GETCAPTURE", "GetCapture"),
    ("GETCURSORINFO", "GetCursorInfo"), ("CLIPCURSOR", "ClipCursor"),
    ("PEEKMESSAGEW", "PeekMessageW"), ("PEEKMESSAGEA", "PeekMessageA"),
    ("GETMESSAGEW", "GetMessageW"), ("GETMESSAGEA", "GetMessageA"),
    ("GETFOREGROUNDWINDOW", "GetForegroundWindow"), ("GETACTIVEWINDOW", "GetActiveWindow"),
    ("GETFOCUS", "GetFocus"), ("GETGUIThreadINFO", "GetGUIThreadInfo"),
    ("SHM_FMT", "Local\\imgbotdx_%u"), ("MUTEX_FMT", "Local\\imgbotdx_%u_lk"),
]


def gen_obf_header(native: Path):
    """生成 obfstrings.gen.h: 构建期随机盐异或密文, dmcore.c 于 DxInit 首
    obf_init() 解码到静态缓冲。每次构建盐随机 → 密文不同 → 二进制哈希随构建
    变(哈希特征一并失效)。防 strings/YARA 类扫描, 不是对抗逆向。"""
    import random
    out = ["/* obfstrings.gen.h -- build.py 自动生成, 勿手改勿提交。",
           " * 遮蔽串: 编译期密文 + obf_init() 解码。防特征扫描(非对抗逆向)。 */",
           "#ifndef OBFSTRINGS_GEN_H", "#define OBFSTRINGS_GEN_H", ""]
    inits = []
    for var, s in OBF_STRS:
        key = [random.randrange(256) for _ in range(4)]
        ct = [b ^ key[i & 3] for i, b in enumerate(s.encode("ascii"))]
        out.append(f"static const unsigned char OBFD_{var}[{len(ct)}] = "
                   f"{{{','.join(map(str, ct))}}};")
        out.append(f"static const unsigned char OBFK_{var}[4] = "
                   f"{{{','.join(map(str, key))}}};")
        out.append(f"static char OBFS_{var}[{len(ct) + 1}];")
        out.append(f"#define OBF_{var} OBFS_{var}")
        out.append("")
        inits.append(f"    _obf_dec(OBFS_{var}, OBFD_{var}, {len(ct)}, OBFK_{var});")
    out.append("static void _obf_dec(volatile char *out, const volatile unsigned char *ct,")
    out.append("                              int n, const volatile unsigned char *key)")
    out.append("{ for (int i = 0; i < n; i++) out[i] = (char)(ct[i] ^ key[i & 3]);")
    out.append("  out[n] = 0; }")
    out.append("static void obf_init(void)")
    out.append("{")
    out.append("    static int done;")
    out.append("    if (done) return;")
    out.append("    done = 1;")
    out.extend(inits)
    out.append("}")
    out.append("#endif")
    (native / "obfstrings.gen.h").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"+ gen obfstrings.gen.h ({len(OBF_STRS)} 串)")


def build(zig: str):
    BIN.mkdir(exist_ok=True)
    gen_obf_header(NATIVE)
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
