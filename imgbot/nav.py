# -*- coding: utf-8 -*-
"""Nav —— 界面导航图(通用机制, 游戏数据由消费项目注册)。

任务代码只声明"我要在哪个界面"(go), 怎么走过去是图的事。两类知识分表:

- 界面注册表 screen(): 一屏一锚点(位置固定的界面必须限矩形, AGENTS 规矩);
  注册序即 where() 扫描序 —— 判别力强的锚点先注册。
- 边表 edge(): 从哪屏到哪屏 + 一个动作函数(组 ops 原语), 走完自动验证落点
  (等目标屏锚点出现); 动作抛错/验证超时都按本边失败重试。

定位两层(缺一不可):
- at(name): 期望验证 —— 单帧一张锚点, 高频便宜;
- where(): 全局重定位 —— same_frame 一帧扫全部锚点, 冷启动/迷路时兜底。

go() 核心循环: 已在目标即返 → 迷路先重定位 → BFS 求路 → 逐边
[打断钩子 → 动作 → 验证落点]。某边重试尽 → 强制重定位: 落在别的屏就重算
路径继续(自愈), 全锚点扫不到 = LostError(黑屏/掉登录, 调用方决定重登)。

图只管界面间移动; 界面内操作(战斗/领奖/强化)不进图。判据一句话:
做完需要"换界面验证"的动作才是边, 不换界面的就是普通 ops 调用。

用法(消费项目, 锚点资产按 nav/屏名.png 备):
    nav = Nav(ops)
    nav.screen("city", "nav/city_hud.png", region=(20, 20, 180, 160))
    nav.screen("dungeon", "nav/dungeon_title.png")
    nav.edge("city", "dungeon",
             lambda: ops.find_tap("nav/dungeon_btn.png", timeout=8))
    nav.on_interrupt(["popup/reward_ok.png"])   # 任意屏都可能冒的, 走边前轰掉
    nav.go("dungeon")                           # 不管现在在哪, 图带路

错误语义: 迷路 = LostError(结构性, 必须处理); 总超时/停止 = 安静 False
(ops 惯例, 失败语义调用方交代); 目标未注册/无可达路径 = ValueError(建图
错误, 启动即大声)。边验证失败与迷路现场自动截图留痕 logs/shots/。
"""
from __future__ import annotations

import time
from typing import Callable, NamedTuple

from .ops import Ops, _stopped


class LostError(RuntimeError):
    """迷路且重定位失败(黑屏/掉登录/更新弹窗?), 需重登或人工介入。"""


class _Screen(NamedTuple):
    name: str
    anchor: str
    region: tuple | None
    threshold: float | None


class _Edge(NamedTuple):
    src: str
    dst: str
    action: Callable[[], object]
    timeout: float
    retries: int


class Nav:
    """一个 Ops 门面一张图: nav = Nav(ops)。机制通用, 界面/边/弹窗全由消费项目注册。"""

    def __init__(self, ops: Ops):
        self.ops = ops
        self._screens: dict[str, _Screen] = {}
        self._edges: dict[str, list[_Edge]] = {}
        self._interrupts: list[str] = []
        self._cur: str | None = None   # 当前假设所在屏(None=迷路, 下次 go 强制重定位)

    # ---- 建图 ----
    def screen(self, name: str, anchor: str, region=None,
               threshold: float | None = None) -> None:
        """注册界面: 一屏一锚点。同名重注册=更新锚点(不用改边表)。"""
        self._screens[name] = _Screen(name, anchor, region, threshold)

    def edge(self, src: str, dst: str, action: Callable[[], object],
             timeout: float = 15.0, retries: int = 1) -> None:
        """注册边。action 组 ops 原语, 返回假/抛错=本边失败(整边重来); timeout
        是动作成功后等目标屏锚点的验证秒数(加载屏算在内); retries=额外重试次数。"""
        for n in (src, dst):
            if n not in self._screens:
                raise ValueError(f"边引用未注册界面: {n}(先 screen() 再 edge())")
        self._edges.setdefault(src, []).append(_Edge(src, dst, action, timeout, retries))

    def on_interrupt(self, pics: list[str]) -> None:
        """通用打断表: 任意界面都可能冒的弹窗(奖励/公告/升级), 每次走边前和
        重定位失败时先轰掉。可多次调用追加。"""
        self._interrupts.extend(pics)

    # ---- 定位 ----
    def at(self, name: str) -> bool:
        """期望验证: 我是不是在这屏(单帧一张锚点; 冻结窗内用冻结帧)。"""
        s = self._screens.get(name)
        if s is None:
            raise ValueError(f"未注册界面: {name}")
        return self.ops.exists(s.anchor, s.region, s.threshold)

    def where(self) -> str | None:
        """全局重定位: 一帧(same_frame 共帧)扫全部锚点, 返回界面名, 全不中 None。
        注册序即扫描序, 判别力强的锚点先注册。"""
        if not self._screens:
            return None
        with self.ops.same_frame():
            for s in self._screens.values():
                if self.ops.exists(s.anchor, s.region, s.threshold):
                    return s.name
        return None

    @property
    def cur(self) -> str | None:
        """当前假设所在屏(None=迷路); go 成功后即目标屏。"""
        return self._cur

    # ---- 行走 ----
    def go(self, name: str, timeout: float = 120.0, stop=None) -> bool:
        """走到目标界面: 到达 True; 总超时/停止安静 False; 迷路抛 LostError;
        无可达路径/目标未注册 ValueError(建图错误, 启动即大声)。
        崩溃重启后从任意屏调它, 自动找回位置(挂机长跑的恢复线)。"""
        if name not in self._screens:
            raise ValueError(f"未注册界面: {name}")
        deadline = time.monotonic() + timeout
        while True:
            if _stopped(stop) or time.monotonic() >= deadline:
                return False
            if self.at(name):
                self._cur = name
                return True
            if self._cur is None:
                self._cur = self._relocalize()
                continue          # 重定位可能就落在目标, 回环头复查
            path = self._route(self._cur, name)
            if path is None:
                raise ValueError(f"导航图无可达路径: {self._cur} -> {name}(缺边?)")
            if not path:
                continue          # 环头刚查过不在目标(锚点抖动), 重定位换条路
            self._cur = path[0].dst if self._walk(path[0], deadline, stop) else None

    # ---- 内部 ----
    def _route(self, src: str, dst: str) -> list[_Edge] | None:
        """BFS 最短路(界面几十个, 无需权重); 不可达返回 None。"""
        if src == dst:
            return []
        seen, queue = {src}, [[e] for e in self._edges.get(src, [])]
        while queue:
            path = queue.pop(0)
            if path[-1].dst == dst:
                return path
            for e in self._edges.get(path[-1].dst, []):
                if e.dst not in seen:
                    seen.add(e.dst)
                    queue.append(path + [e])
        return None

    def _walk(self, e: _Edge, deadline: float, stop) -> bool:
        dst = self._screens[e.dst]
        for _ in range(e.retries + 1):
            if _stopped(stop) or time.monotonic() >= deadline:
                return False
            self._dismiss_interrupts()
            try:
                if not e.action():
                    continue       # 动作没做成(按钮没找到等), 整边重来
            except Exception:
                continue           # 动作抛错(时序/遮挡)也按本边失败重试
            if self.ops.wait_pic(dst.anchor, dst.region, timeout=e.timeout,
                                 threshold=dst.threshold, stop=stop):
                return True        # 落点验证过(wait_pic 超时自动留痕)
        return False

    def _relocalize(self) -> str:
        name = self.where()
        if name is None:           # 弹窗可能遮住锚点: 轰掉再扫一次
            self._dismiss_interrupts()
            name = self.where()
        if name is None:
            self.ops.shot("nav_lost")
            raise LostError("迷路且重定位失败(黑屏/掉登录/更新弹窗?), 需重登或人工")
        return name

    def _dismiss_interrupts(self):
        if not self._interrupts:
            return
        with self.ops.same_frame():            # 一帧探测是哪个弹窗
            which = self.ops.probe(*self._interrupts)
        if which:
            self.ops.find_tap(which, timeout=0)  # 现帧点掉(没了=自己消失, 无害)
