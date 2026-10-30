# -*- coding: utf-8 -*-
"""Ops —— 图色门面(DmSoft 之上的高频组合层, A/B/C/E 四族)。

设计规矩(违反会直接抛错, 不是建议):
- 一切等待必有 timeout, 超时安静返回 None/False, 失败语义调用方交代;
- 等待族每轮取**新帧**(冻结窗里调等待 = 复读旧照片, 直接拒绝);
- same_frame 窗口内查 N 样东西共用一帧(省截图 + 判断出自同一瞬间);
- 停止信号先于命中检查(历史 bug: 已叫停但模板恰在屏上, 照样点了出去);
- wait_pic/wait_pic_any/wait_text 超时自动 shot() 留痕(logs/shots/)。

通道无关: 底下 BindWindow 选什么模式(normal/gdi/windows/dx)门面不关心,
帧一律客户区坐标 BGR。裸输入(D 族)不进门面, 直接用 dm.MoveTo/dm.LeftClick。
"""
from __future__ import annotations

import hashlib
import time
from contextlib import contextmanager
from pathlib import Path

import cv2
import numpy as np

from . import ocr, readout, vision
from .dmsoft import _bgr

SHOT_DIR = Path("logs/shots")


class Ops:
    """一个 DmSoft 绑定一个门面: ops = Ops(dm)。"""

    def __init__(self, dm):
        self.dm = dm
        self._frozen: list[np.ndarray] = []   # 帧窗口栈(同一帧多查用)

    # ================= E. 编排族(地基 + 直用) =================
    def wait_until(self, fn, timeout: float, interval: float = 0.3,
                   stop=None) -> bool:
        """通用轮询底座: fn() 为真即返回 True, 超时 False。
        每轮先查停止再看条件(停止优先); 窗口失效异常向上抛。"""
        if self._frozen:
            raise RuntimeError("等待族不能进 same_frame 窗口(复读旧帧)")
        deadline = time.monotonic() + timeout
        while True:
            if _stopped(stop):
                return False
            if fn():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(interval)

    @contextmanager
    def same_frame(self):
        """一帧多查窗口: 进窗截一帧并冻结, 窗内所有查询(exists/probe/check_*/
        find_all/read_*)共用它; 出窗后查询各自现截。点完要看新画面的查找写在窗外。"""
        self._frozen.append(self.dm._screen())
        try:
            yield self
        finally:
            self._frozen.pop()

    def retry(self, fn, times: int = 3, backoff: float = 1.0) -> bool:
        """动作级重试: fn() 返回真即成功; 失败退避 backoff*i 秒后整动作重来。
        管"做到为止"(有副作用), 等待用 wait_until。"""
        for i in range(times):
            if i:
                time.sleep(backoff * i)
            if fn():
                return True
        return False

    def shot(self, tag: str = "") -> Path:
        """当前帧(或冻结帧)存档到 logs/shots/时间_标签.png, 返回路径。
        imencode+write_bytes: cv2.imwrite 在 Windows 中文路径下静默失败(vision.load
        同坑同解), 本库 tag/文本以中文为常态。"""
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SHOT_DIR / f"{time.strftime('%m%d_%H%M%S')}_{tag or 'shot'}.png"
        ok, buf = cv2.imencode(".png", self._frame())
        if ok:
            path.write_bytes(buf.tobytes())
        return path

    # ================= A. 等待族 =================
    def wait_pic(self, pic, region=None, timeout: float = 30,
                 interval: float = 0.4, threshold: float | None = None,
                 stop=None):
        """等图出现, 返回 (中心x, 中心y, 分数); 超时/停止 None(自动留痕)。"""
        hit = None

        def check():
            nonlocal hit
            hit = self._find_fresh(pic, region, threshold)
            return hit is not None

        if self.wait_until(check, timeout, interval, stop):
            return hit
        self.shot(f"wait_pic_{_leaf(pic)}")
        return None

    def wait_pic_any(self, pics: list[str], region=None, timeout: float = 30,
                     interval: float = 0.4, threshold: float | None = None,
                     stop=None):
        """等一组模板里任意一张出现, 返回 (名字, x, y, 分数); 超时 None(留痕)。"""
        hit = None

        def check():
            nonlocal hit
            hit = self._find_any_fresh(pics, region, threshold)
            return hit is not None

        if self.wait_until(check, timeout, interval, stop):
            return hit
        self.shot("wait_pic_any")
        return None

    def wait_pic_gone(self, pic, region=None, timeout: float = 30,
                      interval: float = 0.4, threshold: float | None = None,
                      stop=None) -> bool:
        """等图消失(加载页结束判定)。消失返回 True, 超时 False。"""
        return self.wait_until(
            lambda: self._find_fresh(pic, region, threshold) is None,
            timeout, interval, stop)

    def find_color(self, color: str, region=None, tol: int = 0) -> list[tuple[int, int]]:
        """区域找全部色块坐标(大漠 FindColorEx 对应物, 单帧)。

        color="RRGGBB" 或 "RRGGBB-DRDGDB"(偏差并入 tol); 冻结窗内用冻结帧。
        """
        (b, g, r), t = _bgr(color)
        return vision.find_color(self._frame(), [(b, g, r)], tol=tol + t,
                                 region=region)

    def wait_color(self, color: str, region=None, timeout: float = 30,
                   interval: float = 0.4, stop=None):
        """等某色出现(色比图快, 转圈/倒计时专用), 返回 (x, y) 或 None。"""
        (b, g, r), tol = _bgr(color)
        hit = []

        def check():
            hit[:] = vision.find_color(self.dm._screen(), [(b, g, r)],
                                       tol=tol, region=region)[:1]
            return bool(hit)

        if self.wait_until(check, timeout, interval, stop):
            return hit[0]
        return None

    def wait_color_gone(self, color: str, region=None, timeout: float = 30,
                        interval: float = 0.4, stop=None) -> bool:
        (b, g, r), tol = _bgr(color)
        return self.wait_until(
            lambda: not vision.find_color(self.dm._screen(), [(b, g, r)],
                                          tol=tol, region=region),
            timeout, interval, stop)

    def wait_text(self, text: str, region=None, timeout: float = 30,
                  interval: float = 0.5, min_ratio: float = 0.7, stop=None):
        """OCR 等文字出现("完成"提示类), 返回 (中心x, 中心y, 相似度) 或 None(留痕)。"""
        hit = None

        def check():
            nonlocal hit
            hit = ocr.find_text(self.dm._screen(), text, region, min_ratio)
            return hit is not None

        if self.wait_until(check, timeout, interval, stop):
            return hit
        self.shot(f"wait_text_{text[:8]}")
        return None

    def wait_stable(self, region, timeout: float = 30, interval: float = 0.3,
                    frames: int = 5, stop=None) -> bool:
        """等区域画面静止(连续 frames 帧哈希一致 = 加载完成)。"""
        if self._frozen:
            raise RuntimeError("等待族不能进 same_frame 窗口(复读旧帧)")
        last, run = None, 0
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if _stopped(stop):
                return False
            h = self._frame_hash(self.dm._screen(), region)
            run = run + 1 if h == last else 0
            if run >= frames - 1:
                return True
            last = h
            time.sleep(interval)
        return False

    def wait_change(self, region, timeout: float = 30, interval: float = 0.3,
                    stop=None) -> bool:
        """等区域发生变化(过场结束/结算弹出), 基线是调用时的第一帧。"""
        base = self._frame_hash(self.dm._screen(), region)
        return self.wait_until(
            lambda: self._frame_hash(self.dm._screen(), region) != base,
            timeout, interval, stop)

    # ================= B. 找点族 =================
    def find_tap(self, pic, region=None, timeout: float = 0,
                 interval: float = 0.4, threshold: float | None = None,
                 verify=None, stop=None) -> bool:
        """找到就拟人点。timeout=0 单帧试; verify=点后要等到出现的图(二次确认,
        需要新帧, 冻结窗里不许用 verify)。点到返回 True, 未找到/验证失败 False。"""
        if verify and self._frozen:
            raise RuntimeError("verify 要点后新帧, 不能在 same_frame 窗口里用")
        if timeout:
            hit = self.wait_pic(pic, region, timeout, interval, threshold, stop)
        else:
            hit = self._find(pic, region, threshold)
        if not hit:
            return False
        self.dm.MoveTo(hit[0], hit[1])
        self.dm.LeftClick()
        if not verify:
            return True
        # 停止信号贯穿到验证阶段; 返回转 bool(等待族语义: 验证失败=False)
        return self.wait_pic(verify, region, timeout=5, stop=stop) is not None

    def tap_until_gone(self, pic, region=None, timeout: float = 10,
                       interval: float = 0.5, threshold: float | None = None,
                       stop=None) -> int:
        """连点直到图消失(确定/领取类), 返回点击次数。先等它出现(至多 5s),
        不出现返回 0; 出现后逐次点击复查, 消失/超时停手。"""
        if not self.wait_pic(pic, region, 5, interval, threshold, stop):
            return 0
        n = 0
        end = time.monotonic() + timeout
        while time.monotonic() < end and not _stopped(stop):
            if not self.find_tap(pic, region, threshold=threshold):
                break
            n += 1
            time.sleep(interval)
        return n

    def swipe_find(self, pic, direction: str, region=None, timeout: float = 30,
                   threshold: float | None = None, stop=None):
        """列表滚动查找: 逐屏向 direction('up'/'down'/'left'/'right') 拖,
        每屏找一次, 出现返回 (x, y); 拖不动了/超时 None。"""
        drag = self._drag_vec(direction)
        end = time.monotonic() + timeout
        while time.monotonic() < end and not _stopped(stop):
            hit = self._find_fresh(pic, region, threshold)
            if hit:
                return hit[0], hit[1]
            # 拖到底判定: 拖前拖后整屏哈希不变 = 画面没动, 别再空转
            before = self._frame_hash(self.dm._screen(), None)
            self._swipe_once(drag)
            time.sleep(0.3)
            if self._frame_hash(self.dm._screen(), None) == before:
                return None
        return None

    def exists(self, pic, region=None, threshold: float | None = None) -> bool:
        """单帧存在性(冻结窗内用冻结帧)。"""
        return self._find(pic, region, threshold) is not None

    def probe(self, *pics, region=None, threshold: float | None = None) -> str | None:
        """单帧多图探测: 返回命中模板名(多张同屏取分数最高), 全无 None。"""
        hit = self._find_any(pics, region, threshold)
        return hit[0] if hit else None

    def find_all(self, pic, region=None,
                 threshold: float | None = None) -> list[tuple[int, int]]:
        """找全部命中中心坐标(列表项/多目标), 近邻去重。"""
        t = self._tpl(pic)
        if t is None:
            return []
        frame = self._frame()
        th = threshold or vision.DEFAULT_THRESHOLD
        if region:
            frame, ox, oy = _crop(frame, region)
            return [(x + ox, y + oy) for x, y, _ in vision.find_all(frame, t, th)]
        return [(x, y) for x, y, _ in vision.find_all(frame, t, th)]

    # ================= C. 校验读数族 =================
    def check_color(self, x: int, y: int, color: str, tol: int = 0) -> bool:
        """单点比色(冻结窗内用冻结帧), color="RRGGBB" 或 "RRGGBB-DRDGDB"。"""
        (b, g, r), t = _bgr(color)
        return vision.cmp_color(self._frame(), x, y, (b, g, r), tol=tol + t)

    def check_points(self, points: list[tuple], region) -> bool:
        """多点找色单帧校验(动画元素)。points=[(dx,dy,"RRGGBB"[,tol]), ...],
        首点为锚, 全点命中才真 —— 对接规范见 多点找色Pt规范.md。"""
        from .vision import Pt
        pts = []
        for p in points:
            (b, g, r), t = _bgr(p[2])
            pts.append(Pt(p[0], p[1], (b, g, r), t + (p[3] if len(p) > 3 else 0)))
        return vision.find_by_points(self._frame(), region, pts) is not None

    def read_number(self, region, digit_templates) -> int | None:
        """数字读数(小字体别硬 OCR —— 模板逐字 + NMS)。digit_templates:
        {字符: ndarray} 由消费项目用 pixpick 制作(含 0-9 即可)。读不出 None。"""
        frame, _ox, _oy = _crop(self._frame(), region)
        s = readout.read_digits(frame, digit_templates)
        return int(s) if s.strip().lstrip("-").isdigit() else None

    def read_text(self, region=None) -> str:
        """OCR 读区域全文(悬浮世界的文字先裁区域), 行间空格连接。"""
        return " ".join(t for _x, _y, t in ocr.find_lines(self._frame(), region))

    # ================= 内部 =================
    def _frame(self) -> np.ndarray:
        """冻结窗内返回冻结帧, 否则现截。"""
        return self._frozen[-1] if self._frozen else self.dm._screen()

    @staticmethod
    def _tpl(pic):
        """模板名/ndarray → ndarray; 素材缺失返回 None(渐进配齐, 跳过不炸)。"""
        if isinstance(pic, np.ndarray):
            return pic
        try:
            return vision.load(pic)
        except FileNotFoundError:
            return None

    def _find(self, pic, region, threshold):
        t = self._tpl(pic)
        return None if t is None else vision.find(
            self._frame(), t, threshold or vision.DEFAULT_THRESHOLD, region)

    def _find_fresh(self, pic, region, threshold):
        """等待族专用: 永远现截(等待的语义就是要新画面)。"""
        t = self._tpl(pic)
        return None if t is None else vision.find(
            self.dm._screen(), t, threshold or vision.DEFAULT_THRESHOLD, region)

    def _find_any(self, pics, region, threshold):
        th = threshold or vision.DEFAULT_THRESHOLD
        best = None
        for name in pics:                        # 缺素材跳过(渐进配齐)
            hit = self._find(name, region, th)
            if hit and (best is None or hit[2] > best[3]):
                best = (name, hit[0], hit[1], hit[2])
        return best

    def _find_any_fresh(self, pics, region, threshold):
        th = threshold or vision.DEFAULT_THRESHOLD
        best = None
        for name in pics:
            hit = self._find_fresh(name, region, th)
            if hit and (best is None or hit[2] > best[3]):
                best = (name, hit[0], hit[1], hit[2])
        return best

    def _frame_hash(self, frame, region) -> str:
        f = _crop(frame, region)[0] if region else frame
        return hashlib.md5(np.ascontiguousarray(f).tobytes()).hexdigest()

    def _drag_vec(self, direction):
        hwnd = self.dm.GetBindWindow()
        w, h = self.dm.GetClientSize(hwnd)
        cx, cy = w // 2, h // 2
        L = int(min(w, h) * 0.6)
        vec = {"up": (cx, cy + L // 2, cx, cy - L // 2),        # 上滑=内容向上滚
               "down": (cx, cy - L // 2, cx, cy + L // 2),
               "left": (cx + L // 2, cy, cx - L // 2, cy),
               "right": (cx - L // 2, cy, cx + L // 2, cy)}
        if direction not in vec:
            raise ValueError(f"direction 取 {list(vec)}")
        return vec[direction]

    def _swipe_once(self, vec) -> bool:
        """线性插值拖拽(手势轨迹不需要拟人弯度)。"""
        x1, y1, x2, y2 = vec
        self.dm.MoveTo(x1, y1)
        self.dm.LeftDown()
        for i in range(1, 13):
            t = i / 12
            self.dm.MoveTo(round(x1 + (x2 - x1) * t),
                           round(y1 + (y2 - y1) * t))
            time.sleep(0.03)
        self.dm.LeftUp()
        return True


def _stopped(stop) -> bool:
    if stop is None:
        return False
    return stop() if callable(stop) else stop.is_set()


def _crop(frame, region):
    """(x1,y1,x2,y2) → (裁剪帧, 原点x, 原点y); 坐标换算全屏口径用。"""
    x1, y1, x2, y2 = region
    return frame[y1:y2, x1:x2], x1, y1


def _leaf(pic) -> str:
    """留痕标签取文件名部分(模板名可带子目录, 不剥则 shot 落进不存在的子目录)。"""
    return Path(pic).name if isinstance(pic, str) else "array"
