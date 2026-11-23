# 0002 — 安卓输入常驻会话(自研驻留件)

状态: 已采纳 (2026-11-23)

## 背景

adb 侧每个动作起一次进程: 点击 med 146ms(往返 43 + 设备端起 JVM 103,雷电9/Android 9
实测;Android 11 及以前 `input` 走 app_process 起 JVM,12 起改 `cmd input`,AOSP
cmds/input 各版本源码核实),截图 med 213ms。魔神线轮询 0.4–0.5s,"出现→点到"被这两项
拖住。且 `input tap` 按压 0ms(AOSP sendTap 按下抬起同时间戳)、`input swipe` 直线匀速
(lerp 插值),按住拖动/多点/贝塞尔无法表达。

## 决策

1. **自研驻留件**(约百行 Java,`app_process` 以 shell 身份常驻)+ 宿主侧常驻 adb 进程 +
   管道行协议,合成 `AdbTarget` 的 `input="session"` 通道(术语见 GLOSSARY「常驻会话」
   「驻留件」「shell 路」)。不用 scrcpy-server(附带不用的视频流面,协议随上游大版本快
   变,2026 年已发三个大版本);不用三方 Python 封装(2026-11-23 逐一核过: py-scrcpy-client
   停在 server 1.20 协议且钉死 Python<3.11/OpenCV<5;wetest-scrcpy 无许可证、源码在腾讯
   内网;adb_scr_py 2 star 无自动重连;airtest 与本仓 numpy2/cv5 互斥)。
2. **注入接口**: `InputManager.injectInputEvent` —— 与系统 `input` 命令同一接口(shell
   uid 持 INJECT_EVENTS,platform.xml/Shell manifest 核实)。Android 9–13 实例与方法均
   公开,直接调用;14+ 挪入 InputManagerGlobal,反射分支(本机未验,标注)。事件构造
   (MotionEvent.obtain 参数/setSource/setDisplayId)逐字段照抄 AOSP `Input.java`,与系统
   行为一致。注入模式 ASYNC(scrcpy 同款;WAIT_FOR_FINISH 同步等待拖慢吞吐)。公开 API
   `Instrumentation.sendPointerSync` 弃用: 14 上把事件定向到调用者自身 uid 的窗口(点不中
   目标)、无 Looper 即抛、强制 WAIT_FOR_FINISH(三版本源码核实)。
3. **管道行协议**(adb shell stdin/stdout)而非本地 socket: 免端口转发与端口占用面
   (本机 winnat 保留段 5458–5857 曾盖住 5555 的事故),adb 自带多设备路由。文本行一问一答。
4. **默认 `input="shell"`**(零行为变化);会话建立失败/中断自动重建一次,仍败永久回落
   shell 路并记 warn。行为契约: swipe 同直线同时长、tap 按压 0ms,与会话协议内预置的
   down/move/up 原语分离 —— 手势升级不动驻留件。
5. **验收**: 雷电9 点击 med ≤20ms;shell/会话两路等价(点击/滑动/BACK 前后像素差对比);
   杀驻留件自动回落;selfcheck 绿。MuMu12(Android 12)本机未装,标「未验收」,后续在
   有 MuMu 的机器跑 `scripts/adb_session_smoke.py` 补验。

## 后果

- 引入 Java 源码与 d8 构建依赖(开发机一次性,不入仓);产物 jar 随库分发
  (imgbot/assets/,同 ADBKeyboard.apk 惯例),消费项目免构建工具。
- 设备多一个驻留文件与常驻进程(/data/local/tmp,随绑定存亡;不装 apk、不改系统设置,
  比 ADBKeyboard 轻)。
- 隐藏 API 风险收敛于 14+ 分支(未验);9–13 为公开 API。
- 会话状态成为新故障面: 断线回落保底,最坏等于现状 shell 路。
