"""imgbot: 双目标(PC 窗口 + 安卓模拟器)图色脚本基础库.

- 方法决策树/验收门槛/制作分工 → 仓库 AGENTS.md(动手前必读)
- 色键模板约定/与大漠命令的对照 → README.md
- Target 双后端(大漠 BindWindow 思想) → imgbot.target
"""
# 惰性子模块(PEP 562): import imgbot 不再连带 cv2/numpy —— 只要 dx.host /
# win32.window 的消费方(ctypes+numpy 面)可瘦身打包, 交互式用法不变
# (`from imgbot import vision` / `imgbot.vision.find` 均照常)。
_SUBMODULES = ("adb", "actions", "ime", "ocr", "readout", "vision", "target")


def __getattr__(name):
    if name in _SUBMODULES:
        import importlib
        mod = importlib.import_module(f".{name}", __name__)
        globals()[name] = mod
        return mod
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(_SUBMODULES))
