"""模板逐字读数管线(LZG_PMH 移植, RGB→BGR/灰度归一).

游戏内小字体(~14px)OCR 与原生分辨率模板双路都难直接读: 放大 4x 后再逐字模板匹配,
按分数优先的 NMS 去重(同一 x 区间只留最高分, 跨变体全局竞争), 再按 x 排序读出字符串.
模板样本与裁剪框由消费项目标定, 本模块不硬编码任何坐标/阈值.
"""
from __future__ import annotations

import cv2
import numpy as np


def enlarge(scene, factor: int = 4):
    """小字体放大(4x 后才检得出)."""
    return cv2.resize(scene, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)


def _match_all(scene_bgr, tpl, threshold: float):
    """灰度+掩码多匹配(与 vision.find 同预处理), 连通域取峰.
    返回 [(x, y, w, h, score)](x/y 为模板左上角), 供逐字 NMS 用."""
    gray = cv2.cvtColor(scene_bgr, cv2.COLOR_BGR2GRAY)
    if tpl.ndim == 3 and tpl.shape[2] == 4:
        tg = cv2.cvtColor(tpl[:, :, :3], cv2.COLOR_BGR2GRAY)
        mask = tpl[:, :, 3]
    elif tpl.ndim == 3:
        tg = cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)
        mask = None
    else:
        tg, mask = tpl, None
    if float(tg.std()) < 1e-6:
        return []
    res = cv2.matchTemplate(gray, tg, cv2.TM_CCOEFF_NORMED) \
        if mask is None else cv2.matchTemplate(gray, tg, cv2.TM_CCOEFF_NORMED, mask=mask)
    res[~np.isfinite(res)] = -1.0
    m = (res >= threshold).astype(np.uint8)
    n, _, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    h, w = tg.shape
    out = []
    for i in range(1, n):                      # 0 是背景
        rx, ry, rw, rh, _area = stats[i]
        local = res[ry:ry + rh, rx:rx + rw]
        dy, dx = np.unravel_index(int(local.argmax()), local.shape)
        if float(gray[ry + dy:ry + dy + h, rx + dx:rx + dx + w].std()) < 1e-6:
            continue                           # 零方差命中区 = 纯色虚高, 剔除
        out.append((int(rx + dx), int(ry + dy), w, h, float(local[dy, dx])))
    return out


def read_digits(scene_bgr, templates, threshold: float = 0.8) -> str:
    """模板逐字识别. templates: {字符: ndarray(BGR)} 或 {字符: [ndarray, ...]}
    (一字符多变体); 返回按 x 排序拼接的字符串.

    黑底上空区也会给出中等虚高分, 故采用按分数优先的非极大值抑制(NMS):
    同一位置(x 区间重叠)只留最高分的命中(跨变体全局竞争), 再按 x 排序读出.
    """
    hits = []                                    # (x, w, score, char)
    for char, tmpl in templates.items():
        for variant in (tmpl if isinstance(tmpl, list) else [tmpl]):
            for x, y, w, h, score in _match_all(scene_bgr, variant, threshold):
                hits.append((x, w, score, char))
    accepted = []
    for x, w, score, char in sorted(hits, key=lambda t: -t[2]):
        # x 区间 [x, x+w) 与已收留区间重叠 -> 分数较低, 丢弃
        if any(x < ax + aw and ax < x + w for ax, aw, _s, _c in accepted):
            continue
        accepted.append((x, w, score, char))
    accepted.sort(key=lambda t: t[0])
    return "".join(char for _x, _w, _s, char in accepted)
