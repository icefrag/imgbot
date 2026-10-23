# 多点找色 · Pt 入参规范(pixpick ↔ find_by_points 对接契约)

2026-10-02 定稿。消费方:库侧 `imgbot/vision.py` 的 `find_by_points` / `bot/ops.py` 的 `find_points`。
生产方:pixpick「选点找色」页(`D:/script-workspace/tools/pixpick`, 独立项目)。
**任一侧改动必须同步本文件 + 两侧自检**(验收见文末)。

## 消费方签名

    vision.find_by_points(screen_bgr, rect, points, direction=0) -> (x, y) | None
    ops.find_points(w, rect, points, direction=0, screen=None)   # 业务层入口, 自动截图

- `rect=(x1,y1,x2,y2)` 搜索矩形, **尽量小别全屏**(pixpick 按点阵外扩 12px 生成);
- `points` 为 `Pt` 列表, **第一个点是锚点**(通常 `Pt(0, 0, 色, tol)`);
- **全部点命中才返回**锚点全屏坐标 (x, y), 无达标候选返回 None;
- `direction`: 有多个达标候选时返回哪个 — 0 左上起(默认) 1 左下起 2 右上起 3 右下起。

## Pt 字段(`from imgbot.vision import Pt`, NamedTuple)

| 字段 | 类型 | 默认 | 含义 | pixpick 怎么填 |
|---|---|---|---|---|
| dx, dy | int | 必填 | 相对锚点的偏移(锚点自身即 (0, 0)) | 点坐标减锚点坐标 |
| col | (B,G,R) | 必填 | 基准色 | 放大镜下点选像素的 BGR |
| tol | int 或 (tB,tG,tR) | 8 | 逐通道容差; 标量=三通道同值 | 容差旋钮(默认 25) |
| alts | ((B,G,R), ...) | () | 变体色: 与 col **任一命中即该点命中** | 不填 — 动画相位色粘贴后手工补 |
| anti | bool | False | 反色: col+alts **全不命中才算命中** | 不填 — "特效来了才变色"的点位粘贴后手工补 |

## 命中判定细则

1. 单点命中 = 像素与任一变体色(col 或 alts 之一)逐通道差 ≤ tol;
2. anti=True 取反: 全部变体色都不命中才算命中(典型: 特效盖过来该点才变亮的点位);
3. 伴点落到 rect 外按未命中;
4. 全部点命中的候选按 direction 排序取第一个; 锚点在 rect 内按其变体色向量化筛出候选集。

## pixpick 输出格式(format_output 必须逐字符合)

```python
from imgbot.vision import Pt
pts = [
    Pt(0, 0, (61, 62, 227), 25),    # 锚点
    Pt(15, 3, (30, 80, 240), 25),
]
rect = (88, 138, 212, 262)
```

动画相位色 / 反色手工补充示例:

    Pt(15, 3, (30, 80, 240), 25, alts=((90, 40, 200), (10, 120, 250)))   # 一个点闪 3 个相位色
    Pt(4, 0, (250, 220, 30), 20, anti=True)   # 特效来了才变这个色的点, 平时必须不是

## 选点规矩(AGENTS.md 决策树 #4 的展开)

- 3~5 个点, 全选**不参与动画**的稳定像素, 避开光效/高光/数字区; 点间拉开距离覆盖不同部位;
- 锚色选元素里**最稀有**的颜色(候选集越小越快越稳); 元素会闪的颜色改走 alts 变体, 别硬选;
- rect 能小则小; 命中语义是**全点必须命中**, 每个点都是一票否决, 宁少勿滥。

## 验收(改格式必跑)

- pixpick 侧: `uv run pixpick.py --selftest`(format_output 逐字断言);
- 脚本侧: `uv run python selfcheck.py`(多点找色段: 范围/空点阵/全点命中/变体/逐通道容差/反色/方向)。

## 取舍记录(2026-10-02, 为什么不是大漠字符串)

借鉴大漠 FindMultiColor 的**能力**: 一点多色变体、反色、逐通道偏色、四方向查找。
不抄其**字符串 DSL**("x|y|RRGGBB-DRDGDB|..."): 那是 COM 插件跨语言调用的历史包袱,
pixpick 直接吐 `Pt(...)` 字面量, 所见即所得可粘贴。删除的两样: `sim` 全局相似度
(原生 API 下容差逐点直接写, pixpick 旋钮生成时已填), `ratio` 部分命中(动画场景由
变体色正解, 全点命中语义更严格、更可预测)。
