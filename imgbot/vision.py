"""视觉层: 模板匹配 + 多点找色 + 区域找色 —— 分辨率由消费项目约定.

元素该用哪种识别方法见仓库 AGENTS.md 的决策树(文字→OCR / 实底→模板 /
悬浮动态背景→色键模板 / 本体带动画→多点找色 / 单点校验→cmp_color).
通道序一律 BGR(cv2/adb/PrintWindow 后端统一)。
"""
from __future__ import annotations

import math
import time
from pathlib import Path
from typing import NamedTuple

import cv2
import numpy as np

DEFAULT_THRESHOLD = 0.85
TEMPLATES_DIR = Path("templates")  # 消费项目入口调 set_templates_dir 指到自己的模板目录
KEY_CORNER_TOL = 8   # 四角互认同色的逐通道容差(≥3/4多数票, 容忍一个角被笔画/光晕污染)
KEY_DIST = 8         # 键控距离: 与底色逐通道差≤此值视为透明(底色由工具实心填充)
FLAT_EPS = 1e-6      # 零方差判定: TM_CCOEFF_NORMED 在纯色块上会满分虚高(LZG_PMH 实证)

_tpl_cache: dict[str, np.ndarray] = {}


def set_templates_dir(path) -> None:
    """模板目录注入(每个项目在入口处设一次, 默认 ./templates 相对 CWD)."""
    global TEMPLATES_DIR
    TEMPLATES_DIR = Path(path)


def _auto_key(img: np.ndarray) -> np.ndarray:
    """「四角即底色」约定: 3通道模板 ≥3/4 角同色 → 该色是背景,
    载入时自动键控成 RGBA, 匹配只比非底色笔画. 多数票容忍一个角被笔画/光晕污染
    (实测 entry_enter 左上角混入按钮光晕, 手动 key_out_background 曾因此拒收)."""
    corners = [img[0, 0], img[0, -1], img[-1, 0], img[-1, -1]]
    bg, vote = None, 0
    for c in corners:
        n = sum(1 for o in corners
                if int(np.abs(o.astype(int) - c.astype(int)).max()) <= KEY_CORNER_TOL)
        if n > vote:
            vote, bg = n, c
    if vote < 3:
        return img
    dist = np.abs(img.astype(int) - bg.astype(int)).max(axis=2)
    alpha = np.where(dist <= KEY_DIST, 0, 255).astype(np.uint8)
    return np.dstack([img, alpha])


def decode_png(png: bytes) -> np.ndarray:
    """adb 截图 bytes -> BGR ndarray."""
    arr = np.frombuffer(png, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"截图解码失败 ({len(png)} bytes)")
    return img


def load(name: str) -> np.ndarray:
    """加载模板(保持原通道). 按文件名缓存. name 相对模板目录.

    「四角即底色」约定: 3通道模板四角(≥3/4)同色时载入即自动键控成 RGBA(alpha掩码),
    匹配只比笔画 — 见 _auto_key. 手动 key_out_background 变为可选(载入已自动).
    走 read_bytes+imdecode: cv2.imread 在 Windows 含中文的路径下不可靠.
    """
    path = TEMPLATES_DIR / name
    key = str(path)
    if key not in _tpl_cache:
        buf = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
        if img is None:
            raise FileNotFoundError(f"模板不存在或解码失败: {path}")
        if img.ndim == 3 and img.shape[2] == 3:
            img = _auto_key(img)
        _tpl_cache[key] = img
    return _tpl_cache[key]


def key_out_background(name: str, tol: int = 8) -> float:
    """色键模板约定: 模板"四个角像素同色"即代表该色是背景, 本函数把它键控成 RGBA 掩码.

    制作此类模板时把不要的背景填成任意统一颜色(推荐品红 RGB 255,0,255),
    四角同色(容差 tol)即整幅按该色抠透明. 返回保留笔画占比.
    注意: 只对"背景特殊填充"的模板调用; 普通实底模板四角也可能同色(面板色), 勿用.
    """
    path = TEMPLATES_DIR / name
    img = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_UNCHANGED)
    if img.ndim == 3 and img.shape[2] == 4:
        return float((img[:, :, 3] > 0).mean())  # 已是 RGBA, 视为处理过
    key = img[0, 0].astype(int)
    for c in (img[0, -1], img[-1, 0], img[-1, -1]):
        if int(np.abs(c.astype(int) - key).max()) > tol:
            raise ValueError(f"{name}: 四角颜色不一致, 不是色键模板")
    dist = np.abs(img.astype(int) - key).max(axis=2)
    alpha = np.where(dist <= tol, 0, 255).astype(np.uint8)
    rgba = np.dstack([img, alpha])
    path.write_bytes(cv2.imencode(".png", rgba)[1].tobytes())
    return float((alpha > 0).mean())


def _prep(tpl: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
    """模板 → (灰度图, 掩码). RGBA 模板带 alpha 掩码(需 OpenCV>=4.5 的 masked CCOEFF_NORMED)."""
    if tpl.ndim == 3 and tpl.shape[2] == 4:
        return cv2.cvtColor(tpl[:, :, :3], cv2.COLOR_BGR2GRAY), tpl[:, :, 3]
    if tpl.ndim == 3:
        return cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY), None
    return tpl, None


class _MatchResult(NamedTuple):
    """模板匹配结果(单一来源 _match 的产物): result=相关系数矩阵, gray=屏幕灰度,
    h/w=模板高宽(命中区零方差判定用)."""
    result: np.ndarray
    gray: np.ndarray
    h: int
    w: int


def _match(screen_bgr: np.ndarray, tpl: np.ndarray) -> _MatchResult | None:
    """模板匹配公共预处理(单一来源): 灰度化 + 掩码 + 零方差拦截 + 非有限值压平.

    纯色模板(零方差)返回 None.
    """
    screen_gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
    tpl_gray, mask = _prep(tpl)
    if float(tpl_gray.std()) < FLAT_EPS:
        return None
    result = cv2.matchTemplate(screen_gray, tpl_gray, cv2.TM_CCOEFF_NORMED) \
        if mask is None else \
        cv2.matchTemplate(screen_gray, tpl_gray, cv2.TM_CCOEFF_NORMED, mask=mask)
    # 退化窗口(掩码/纯色背景)会产生 inf/NaN, 先压掉再取最大, 否则会盖过真实命中位
    result[~np.isfinite(result)] = -1.0
    return _MatchResult(result, screen_gray, tpl_gray.shape[0], tpl_gray.shape[1])


def _flat_hit(screen_gray: np.ndarray, x: int, y: int, h: int, w: int) -> bool:
    """命中区是否纯色(零方差): 相关系数在纯色块上无意义, 一律判未命中."""
    return float(screen_gray[y:y + h, x:x + w].std()) < FLAT_EPS


def find(screen_bgr: np.ndarray, tpl: np.ndarray,
         threshold: float = DEFAULT_THRESHOLD) -> tuple[int, int, float] | None:
    """单目标模板匹配, 返回(中心x, 中心y, 分数); 低于阈值返回 None.

    零方差防虚高(LZG_PMH 移植): 纯色模板/纯色命中区上的相关系数无意义, 一律判未命中.
    """
    m = _match(screen_bgr, tpl)
    if m is None:
        return None
    _, score, _, loc = cv2.minMaxLoc(m.result)
    if not math.isfinite(score) or score < threshold:
        return None
    if _flat_hit(m.gray, loc[0], loc[1], m.h, m.w):
        return None
    return loc[0] + m.w // 2, loc[1] + m.h // 2, score


def find_any(screen_bgr: np.ndarray, names: list[str],
             threshold: float = DEFAULT_THRESHOLD) -> tuple[int, int, float] | None:
    """多模板任一命中: 逐个 find, 返回分数最高的命中(坐标即该模板的中心), 全部未过阈值 None.

    用于同一道具有多种外观状态(如道具图标的获得角标差异). 列表里文件缺失的模板直接
    跳过 —— 素材可以渐进添加, 不用一次配齐."""
    best = None
    for name in names:
        try:
            tpl = load(name)
        except FileNotFoundError:
            continue
        hit = find(screen_bgr, tpl, threshold)
        if hit and (best is None or hit[2] > best[2]):
            best = hit
    return best


class Pt(NamedTuple):
    """多点找色的一个采样点(pixpick「选点找色」页输出即此格式, 对接规范见仓库根 多点找色Pt规范.md)."""
    dx: int
    dy: int
    col: tuple[int, int, int]                 # 基准色 BGR
    tol: int | tuple[int, int, int] = 8       # 容差: 标量=三通道同值, 三元组=逐通道
    alts: tuple = ()                          # 变体色(动画相位色), 与 col 任一命中即该点命中
    anti: bool = False                        # 反色: col+alts 全不命中才算命中


def _tol3(tol) -> np.ndarray:
    """容差规格化成逐通道 (3,) 数组."""
    a = np.asarray(tol, dtype=int)
    return np.full(3, int(a)) if a.ndim == 0 else a


def find_by_points(screen_bgr: np.ndarray, rect: tuple[int, int, int, int],
                   points: list[Pt], direction: int = 0) -> tuple[int, int] | None:
    """矩形区域内多点找色: 元素本体带动画特效、模板匹配不稳时的替代方案(按键精灵/大漠同款思路).

    rect=(x1,y1,x2,y2) 搜索范围, 尽量小别全屏; points 为 Pt 列表, 第一个点是锚点(通常
    Pt(0,0,色,tol)), 其余点 dx/dy 为相对锚点的偏移. 单点命中 = 像素与任一变体色(col+alts)
    逐通道差≤tol, anti=True 反相(全不命中才算, 排"特效来了才变色"的点位); 伴点越界按未命中.
    全部点命中才返回锚点全屏坐标, 无达标候选返回 None. direction: 多个达标候选时返回哪个 —
    0 左上起(默认) 1 左下起 2 右上起 3 右下起. 选点避开参与动画的区域(光效/高光),
    动画相位色进 alts.
    """
    if not points:
        return None
    if direction not in (0, 1, 2, 3):
        raise ValueError(f"direction 只支持 0~3: {direction}")
    x1, y1, x2, y2 = rect
    sub = screen_bgr[y1:y2, x1:x2].astype(int)
    h, w = sub.shape[:2]
    prepped = [(p.dx, p.dy, [np.asarray(c, int) for c in (p.col, *p.alts)], _tol3(p.tol), p.anti)
               for p in points]
    # 一级筛选: 锚点任一变体色的候选像素(向量化); 二级: 逐候选验证全部点
    amask = np.zeros((h, w), bool)
    adx, ady, avars, atol3, _ = prepped[0]
    for c in avars:
        amask |= (np.abs(sub - c) <= atol3).all(axis=2)
    ys, xs = np.nonzero(amask)
    if ys.size == 0:
        return None
    sy, sx = ((1, 1), (-1, 1), (1, -1), (-1, -1))[direction]
    cands = sorted(zip(ys.tolist(), xs.tolist()), key=lambda t: (sy * t[0], sx * t[1]))
    n = len(prepped)
    for y, x in cands:
        ax, ay = x - adx, y - ady  # 反推锚点位置(锚点自身也可带偏移)
        hits = 0
        for dx, dy, variants, tol3, anti in prepped:
            px, py = ax + dx, ay + dy
            ok = False
            if 0 <= px < w and 0 <= py < h:
                ok = any((np.abs(sub[py, px] - c) <= tol3).all() for c in variants)
                if anti:
                    ok = not ok
            if ok:
                hits += 1
        if hits == n:
            return x1 + ax, y1 + ay
    return None


def find_all(screen_bgr: np.ndarray, tpl: np.ndarray, threshold: float = DEFAULT_THRESHOLD,
             min_dist: int = 12) -> list[tuple[int, int, float]]:
    """多目标模板匹配: 返回所有 ≥threshold 的命中(中心x,中心y,分数), 按 x 排序, 近邻去重.

    用于数个数类的场景(如任务星级的一排金星), 单目标用 find.
    """
    m = _match(screen_bgr, tpl)
    if m is None:
        return []
    result = m.result
    hits: list[tuple[int, int, float]] = []
    # 从最高分往下收, 收一个把邻近区域压掉(非极大值抑制)
    while True:
        _, score, _, loc = cv2.minMaxLoc(result)
        if score < threshold:
            break
        if not _flat_hit(m.gray, loc[0], loc[1], m.h, m.w):
            hits.append((loc[0] + m.w // 2, loc[1] + m.h // 2, round(score, 3)))
        x0, x1 = max(0, loc[0] - min_dist), loc[0] + min_dist
        y0, y1 = max(0, loc[1] - min_dist), loc[1] + min_dist
        result[y0:y1, x0:x1] = -1.0
    hits.sort(key=lambda t: t[0])
    return hits


def wait_find(screenshot_fn, name: str | np.ndarray, timeout: float = 30,
              interval: float = 0.4, threshold: float = DEFAULT_THRESHOLD,
              stop_event=None) -> tuple[int, int, float] | None:
    """轮询截图直到模板出现; 超时返回 None. screenshot_fn 无参返回 BGR ndarray.
    name 为模板文件名(走 load 缓存)或已加载的模板图.
    interval 0.4s: 截图本身0.2s+, 再快没意义; 原默认0.8s实测让每个弹窗多等半拍(提速实证)."""
    deadline = time.monotonic() + timeout
    tpl = load(name) if isinstance(name, str) else name
    while True:
        if stop_event is not None and stop_event.is_set():
            return None          # 停止优先: 已叫停就不再截图、不再命中(停止≠命中)
        hit = find(screenshot_fn(), tpl, threshold)
        if hit:
            return hit
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


def changed(img_a: np.ndarray, img_b: np.ndarray, diff: float = 6.0) -> bool:
    """两屏是否明显不同(缩略灰度均值差): 用于判断加载/切屏."""
    small_a = cv2.resize(cv2.cvtColor(img_a, cv2.COLOR_BGR2GRAY), (96, 54))
    small_b = cv2.resize(cv2.cvtColor(img_b, cv2.COLOR_BGR2GRAY), (96, 54))
    return float(np.abs(small_a.astype(int) - small_b.astype(int)).mean()) > diff


def is_display_dead(screenshot_fn, timeout: float = 3.0, interval: float = 0.5,
                    diff: float = 6.0) -> bool:
    """画面卡死判定(大漠 IsDisplayDead 对应物): timeout 内画面始终无明显变化 = 死.

    screenshot_fn 无参返回 BGR ndarray. 画面活着(游戏在动/有效渲染)返回 False."""
    ref = screenshot_fn()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(interval)
        if changed(ref, screenshot_fn(), diff):
            return False
    return True


# ---- 区域找色族(LZG_PMH 移植, BGR 归一; 大漠 FindColor/GetColorNum/GetAveRGB/CmpColor 对应物) ----

def _color_mask(sub: np.ndarray, colors, tol) -> np.ndarray:
    """sub: int 化的 (H,W,3) 区域; colors: 单色或色列表(BGR); tol: 标量或逐通道.
    返回 (H,W) bool: 任一颜色全通道落在容差内 —— 防止跨颜色拼通道误中."""
    cols = np.atleast_2d(np.asarray(colors, dtype=int))
    tol3 = _tol3(tol)
    return np.any(np.all(np.abs(sub[:, :, None, :] - cols) <= tol3, axis=-1), axis=-1)


def _region(screen_bgr: np.ndarray, region=None) -> tuple[np.ndarray, tuple[int, int]]:
    h, w = screen_bgr.shape[:2]
    x1, y1, x2, y2 = region if region else (0, 0, w, h)
    return screen_bgr[y1:y2, x1:x2].astype(int), (x1, y1)


def find_color(screen_bgr: np.ndarray, colors, tol=10, region=None) -> list[tuple[int, int]]:
    """区域内找色, 返回全部命中坐标 [(x, y), ...]. colors 单色或色列表(任一命中即算).
    region=(x1,y1,x2,y2), None=全屏. 大色场慎用(返回点可能巨多), 点数用 count_color."""
    sub, (x1, y1) = _region(screen_bgr, region)
    ys, xs = np.nonzero(_color_mask(sub, colors, tol))
    return [(int(x) + x1, int(y) + y1) for y, x in zip(ys, xs)]


def count_color(screen_bgr: np.ndarray, colors, tol=10, region=None) -> int:
    """区域内命中色像素计数: 「亮像素轮询判定」类逻辑的原语(某图标出现→亮像素数上台阶)."""
    sub, _ = _region(screen_bgr, region)
    return int(_color_mask(sub, colors, tol).sum())


def color_at(screen_bgr: np.ndarray, x: int, y: int) -> tuple[int, int, int]:
    """取单点颜色 (B, G, R)."""
    b, g, r = screen_bgr[y, x][:3]
    return (int(b), int(g), int(r))


def cmp_color(screen_bgr: np.ndarray, x: int, y: int, colors, tol=8) -> bool:
    """单点比色(大漠 CmpColor 对应物): (x,y) 处颜色与 colors(单色或列表, 任一)在容差内."""
    pt = np.asarray(screen_bgr[y, x][:3], dtype=int)
    cols = np.atleast_2d(np.asarray(colors, dtype=int))
    return bool(np.any(np.all(np.abs(pt - cols) <= _tol3(tol), axis=-1)))


def ave_color(screen_bgr: np.ndarray, region=None) -> tuple[float, float, float]:
    """区域平均色 (B, G, R). 大漠 GetAveRGB 对应物; 亮度门槛类判定先取它再 max/mean."""
    sub, _ = _region(screen_bgr, region)
    m = sub.mean(axis=(0, 1))
    return (float(m[0]), float(m[1]), float(m[2]))
