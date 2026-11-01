# imgbot · 双目标图色脚本基础库

PC 窗口(Win32) + 安卓模拟器(adb) 双后端的图色自动化库: 模板匹配 / 色键透明模板 /
多点找色 / 区域找色 / OCR / 模板逐字读数 / 拟人键鼠 / 中文输入; `DmSoft` 大漠接口壳 +
自研「潜影」dx 注入通道(后台图色/键鼠, 不依赖大漠插件)。
源自两个实战项目合并: 模拟器-魔神(安卓侧) + LZG_PMH(PC 侧)。

| 给谁看 | 去哪 |
|---|---|
| AI / 开发规则 / 方法决策树 | `AGENTS.md`(动手前必读) |
| API 速查(按模块函数表 + 大漠对照) | `docs/api.md` |
| 术语(潜影/大漠关系) | `GLOSSARY.md` |
| 多点找色选点对接 | `多点找色Pt规范.md` |

## 快速上手(消费项目)

    # pyproject.toml
    [project]
    dependencies = ["imgbot[ocr]"]        # ocr 可选, 不装不影响 import
    [tool.uv.sources]
    imgbot = { path = "../imgbot" }       # 同机开发; 跨机器改 git+tag

```python
from imgbot.target import AdbTarget, Win32Target
from imgbot.win32.window import GameWindow
from imgbot import vision

t = AdbTarget("127.0.0.1:5555")                          # 安卓模拟器/真机
t = Win32Target(GameWindow.find(class_name="XXX"))       # PC 窗口, 绑定一次全打它

hit = vision.find(t.screenshot(), vision.load("btn.png"))    # (x, y, score) 或 None
t.tap(*hit[:2])
```

## 打包(给用户出安装包)

前置: `uv` + `zig`(默认 `D:/tools/zig/zig.exe`, 换路径看 `imgbot/dx/native/build.py` 头注释)。

```bash
# 1. 回归底线(必跑)
uv run python -m imgbot.selfcheck

# 2. 构建潜影注入件(zig 产物, gitignore 不入库, hatch artifacts 强制收进 wheel)
uv run imgbot/dx/native/build.py

# 3. 版本号: pyproject.toml 的 version 每次出包必加(全球用户 AGENTS 规矩 8)

# 4. 打 wheel(只打 wheel —— 注入件是 gitignore 的二进制, sdist 模型下没意义)
uv build --wheel
```

产物: `dist/imgbot-<版本>-py3-none-any.whl`(已实测潜影 dll/exe 7 件全入包)。
消费项目两种吃法:

    # a. 文件源(推荐, 锁进 uv.lock)
    [tool.uv.sources]
    imgbot = { path = "../imgbot/dist/imgbot-0.2.0-py3-none-any.whl" }

    # b. 直接装
    uv pip install dist/imgbot-0.2.0-py3-none-any.whl

发包前的真机验收按 AGENTS.md「回归只跑影响面」规矩: `scripts/` 下对应烟测
(dx_cursor / dx_keystate / huaxia 等, dmcore/host 协议改动例外, 双烟测+全量 selfcheck)。
