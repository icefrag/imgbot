# 07 · 低频图色函数杂拌 —— FindColorBlock / HSV 族 / CaptureGif / CapturePre / LockDisplay

Status: needs-triage
Category: feature
Reported: 2026-11-25 (大漠后台图色缺口清点, 见 spec.md)

一组低频辅助图色函数, 各子项独立, 出现需求单独开工, 不打包实施:

| 子项 | 大漠函数 | 现状 | 等价凑法(现有) | 何时开工 |
|---|---|---|---|---|
| 色块找色 | FindColorBlock/Ex(区域色占比达标即命中) | 无 | count_color 在 region 内手动判阈值 | 需要反向"按占比找块"时 |
| HSV 均值 | GetAveHSV | 无 | ave_color + cv2 调用方自转 cvtColor | HSV 判色真需求出现 |
| 颜色转换 | RGB2HSV/HSV2RGB/RGB2Gray | 无 | cv2 一行, 不搬(红线: 库不带 cv2 包装) | 不搬, 除非壳层语义需要 |
| 录屏 | CaptureGif | 无 | 无 | 挂机取证/复现需求出现 |
| 预截图 | CapturePre(截存下一帧供比对) | 无 | ops.same_frame 一帧多查已覆盖主要场景 | 不搬, 除非比对语义需要跨帧 |
| 锁定帧 | LockDisplay(锁定画面不刷新防撕裂) | 显式 raise 占位(dmsoft.py:179) | same_frame 冻结帧语义近似 | 撕裂真造成误判时 |

## 备注

- 红线确认: 字库(FindStr 族)与内存图(FindPicMem 族)不在本清单 —— 已有
  决策不搬(api.md "明确不学"行), 别在杂拌工单里翻案。
- RGB2HSV 类纯转换不进库(AGENTS.md 简洁原则: cv2 调用方一行事)。
