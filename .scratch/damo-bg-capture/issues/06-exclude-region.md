# 06 · SetExcludeRegion —— 找图找色全局排除区

Status: needs-triage
Category: feature
Reported: 2026-11-25 (大漠后台图色缺口清点, 见 spec.md)

## 缺口

大漠 `SetExcludeRegion(type, x1, y1, x2, y2)`: 设置排除区域, 之后所有
图色/文字函数在该区域内视为无效(找不到)—— 用于屏蔽鼠标位置/固定特效
干扰区(如自绘光标恰好停在按钮上时误报)。

我们现状: vision 层只有 `region` **限定**(缩小搜索范围), 没有反向
**排除**(全屏搜但挖掉一块)。库内等价物不存在, 消费项目如遇此场景只能
自己前后两次匹配。

## 方案草案

- vision 层: `find`/`find_all`/`find_color`/`find_by_points` 的 region
  之外加可选 `exclude` 参数; 实现最省路 = 匹配前把 exclude 区域涂成
  无法命中的哨兵值(纯黑+模板四角键控免疫即可), 不改匹配算法本体。
- dmsoft 壳: `SetExcludeRegion(type, x1, y1, x2, y2)` 记账到绑定状态,
  图色方法调用时下传 vision(壳层记账, vision 无全局状态 —— 与现架构
  一致: 壳是大漠语义, vision 是无状态层)。
- type 语义按大漠: 0=清除, 1=排除生效(具体取值以大漠文档为准, 开工时
  对照 docs/ref)。

## 验收

1. 同屏: exclude 区内放目标模板, 未设排除时命中, 设后归零; 区外命中不受影响。
2. SetExcludeRegion(0,...) 清除后恢复命中。

## Comments

### 2026-11-25 · 落地(agent)

- vision 层: `find`/`find_all`/`find_color`/`count_color`/`find_by_points` 加 `exclude=(x1,y1,x2,y2)`
  参数, 实现走**坐标过滤**(命中点落区内丢弃; count_color 掩码扣区; find_by_points 锚点丢弃+伴点
  无效不作任何判据含 anti), 不改匹配本体 —— `_in_excl` 单一来源(vision.py)。
- 壳层: `DmSoft.SetExcludeRegion(type,x1,y1,x2,y2)`(1 开/0 关)记 `self._exclude`, 下传
  FindPic/FindColor 族/FindMultiColor/GetColorNum; 对象级状态 UnBind 不清(大漠同义)。
  大漠 SetExcludeRegion 一手原文不在库(docs/ref 只有 BindWindow 族), 签名按工单草案
  (社区通用口径)实现, 遇反例再校。
- selfcheck vision 层 5 断言(命中丢弃/区外无碍/递补下一候选/伴点否决唯一候选/count 扣区)。
- 注: 伴点无效语义 = 落区像素不作判据(anti 也不反成命中), 与大漠"区域内视为无效"同义。

Status: ready-for-human(纯逻辑 selfcheck 全过; 真机干扰区场景由消费项目自然覆盖)
