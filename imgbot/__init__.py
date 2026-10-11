"""imgbot: 双目标(PC 窗口 + 安卓模拟器)图色脚本基础库.

- 方法决策树/验收门槛/制作分工 → 仓库 AGENTS.md(动手前必读)
- 色键模板约定/与大漠命令的对照 → README.md
- Target 双后端(大漠 BindWindow 思想) → imgbot.target
"""
from . import adb, actions, ime, ocr, readout, vision  # noqa: F401
