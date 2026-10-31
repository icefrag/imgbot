# AGENTS.md · 图色方法决策准则(imgbot 库)

给使用本库做图色脚本的 AI/人: 给 UI 元素选识别方法时**按此决策树执行, 不许临场发挥**。
背景: 模板匹配对"元素本体像素会变"(闪光动画/高光流转)和"悬浮在动的背景上"天然脆弱;
人眼看再清晰, 整块像素比对也会抖。四种工具各治一种病, 选对工具比调参数重要。

## 库的基本约定(先知道再动手)

- **自研后台插件名: 潜影(qianying)**: dx 通道注入件(dmcore.c: GL/d3d9 截帧+键鼠
  配方+键态钩子)+注入器+共享内存协议+DmSoft 壳四件的合称。今后文档/交流/注释
  一律称"潜影", **禁止以"大漠"指代本库组件**——"大漠"仅指反编译参考的原版商业
  插件(逆向资料语境)。潜影与其关系: 接口词汇/消息配方按反编译对齐, 实现零依赖
  完全自研, 详见 GLOSSARY.md。
- **通道序一律 BGR**(cv2 惯例): adb 截图 / PrintWindow 截图 / 图色函数全链路统一,
  别在中间插 RGB 转换(历史上 RGB/BGR 混用是两套实现合并时最直接的坑)。
- **模板目录由消费项目注入**: 入口处 `vision.set_templates_dir(路径)` 设一次,
  默认 `./templates`(相对 CWD)。库自己不带模板。
- **Target 双后端**(大漠 BindWindow 思想): `AdbTarget(serial)` / `Win32Target(hwnd)`,
  绑定一次, 截图/输入全打它; 通道参数留位, 加新通道不改调用方。
- **拟人两层**: adb 侧 `actions.tap`(±3px 偏移+随机延迟); win32 侧 `MouseKeyboard`
  (贝塞尔轨迹+ease-out)。业务点击一律走拟人层, 裸 tap 留给校准/测试。

## 制作分工(硬规矩: 稳定性第一, 不许蛮干)

按决策树走到 **3(色键透明模板)** 或 **4(多点找色)** 时, 资产由用户制作:
- 3 → 主动开口请用户做透明图(告诉用户: 哪张截图、裁哪块、背景填统一色);
- 4 → 请用户用 pixpick 选点并回传 pts/rect(独立项目 `D:/script-workspace/tools/pixpick`,
  `uv run pixpick.py`, 对接规范见仓库根 `多点找色Pt规范.md`);
- **禁止**默默降级用普通模板凑合。唯一例外: 程序化键控(如亮度判据抠笔画)且通过了
  验收门槛(≥2 状态 + 动画 5 帧)才允许自做, 并在代码注释里写明是程序键控。
拿不准元素属于哪类(背景会不会动/本体闪不闪)时, **问用户, 不要猜**。

## 方法决策树(按顺序问)

1. **要读的信息是文字本身?**(等级/任务文本/倒计时/名字) → **OCR**
   (`ocr.find_text/find_texts`: 截图 → 裁小区域 → OCR → 比例匹配; 模板只用于
   定位区域锚点)。悬浮在世界的文字先裁区域再 OCR, 必要时放大 2 倍。
   **发光/带特效的文字也是文字, 先 OCR**(光晕在笔画周围通常不毁识别);
   实测真认不出(重特效盖住笔画)才降级到 2/4, 并把失败截图存档。
   小字体(~14px)数字读数 → `readout.read_digits`(放大 4x + 模板逐字 + NMS),
   别硬 OCR。

2. **元素是实底不透明 UI?**(弹窗/面板内的按钮、图标) → **普通模板匹配**
   (`vision.find`, 阈值 0.85 不动)。
   元素自带动画(闪光/呼吸灯) → **只裁稳定核心**: 文字笔画、图标主体, 光效区域不进模板。
   **位置固定的 UI(弹窗角上的X/边缘按钮)必须在固定矩形内搜索**(rect 限定, 如
   find_color 的 region), 否则画面其他亮色图案会误报——关闭X全屏搜曾在女神翅膀上
   得 0.94, 限矩形后归零。

3. **元素悬浮在世界画面上/背后的背景会动?** → **色键透明模板**
   (约定: 四角(≥3/4)同色即背景, `vision.load` 载入时**自动键控**成 RGBA, 只比对
   笔画 — 制作规矩见 README「色键模板约定」。范例: 动态背景上 0.99+)。

4. **非文字元素(物品/图标)且动画特效盖在本体上**, 裁核心也救不回 → **多点找色**
   `vision.find_by_points(screen, rect, points, direction)`, points 为
   `vision.Pt(dx,dy,col,tol,alts,anti)` 列表(对接规范见 `多点找色Pt规范.md`),
   首点为锚点, **全点命中**才返回锚点坐标; 动画相位色用 alts 列举(任一变体命中即算),
   anti 反色用于"特效来了才变色"的点位。`rect` 两角坐标围成搜索矩形(**尽量小, 不要全屏**)。
   选点要点: 锚色选元素里最稀有的颜色, 避开光效/高光/数字区; 点间距离拉开, 覆盖元素不同部位。

5. **单点颜色校验/区域色统计**(亮度门槛、亮像素计数、单点比色) → 区域找色族
   `cmp_color / count_color / find_color / ave_color`(别再手写内联版)。

6. **兜底: 轮询重试**(`vision.wait_find`; 消费项目的 ops 层 wait/wait_any)自动跨过
   瞬时低谷帧(动画相位/飘字遮挡)。它救"偶尔匹配不上", 不救"永远匹配不上"——
   后者说明方法选错了, 回到树上换路。"等出现就点"的循环**别手写**, 用
   `actions.wait_tap`(wait_find+拟人 tap, 超时安静返回 None, 失败语义调用方交代);
   **一组模板里谁出现点谁**(弹窗表/多种按钮)用 `actions.wait_tap_any`
   (表驱动, `Target`(模板, 固定点击点?, 限矩形?, 阈值?), `until` 集合集齐即提前收口)。

## 一帧多查(frame)使用标准

要在**同一张截图**上判 N 个东西(一帧查完按钮+弹窗+HUD), 消费项目的 ops 层应有
截图缓存窗口(魔神范本 `bot/ops.frame`):

- 窗口内所有查找共用第一帧, 省截图耗时 且判断出自同一瞬间;
- **禁止手工传 screen 变量拼一帧多查** —— 把循环外的帧拿进循环反复匹配,
  "等5秒"变"复读同一张旧照片", 错误是静默的。作用域收口就是防这个;
- 语义 = **严格按键精灵**(用户裁定): 窗口内点了坐标, 之后的查找仍拿旧帧, 开发者自负。
  所以窗口=纯查找批次; **"点完要看新画面"的查找必须出窗口**;
- 别把 sleep/长等待/二级导航(内部要新帧的函数)包进窗口。

## 方法升级规矩(硬性)

- **稳定无干扰的按钮/图标: 模板为主**(快, 30ms 级)。
- **有干扰的元素: 模板定位 + OCR 验证文字, 双确认**(模板给位置, OCR 确认"认对了");
  OCR 也不稳的非文字元素 → 多点找色。
- **同一模板连续多帧分数在阈值附近跳动 → 判定方法选错, 按决策树升级方法。
  禁止降阈值硬凑**(历史教训: 设置按钮 0.85→0.62 一路降, 最后色键掩码一发 0.99)。
- 阈值默认 0.85 不动; 确要调整必须先有测量数据(多帧分数分布)支撑并记录。

## 验收门槛(硬性, 新模板/新方案上线前)

- 在 **≥2 张不同状态的真实截图**上测分并记录: 正常画面 + 遮挡/动画相位画面各一。
- **动画元素连拍 5 帧**看分数分布: 最低帧 ≥ 阈值 + 0.03, 否则按决策树换方法。
- 新识别路径**真机跑通一轮**才算完成, selfcheck 通过只是底线不是验收。
- 失败案例留痕: 误匹配/漏匹配的截图存消费项目 logs/shots/, 换方法时作为对照。

## PC(win32 后端)特有坑(实机结论, 别再踩)

- **UIPI**: 目标以管理员运行时本进程必须同级, 否则 PostMessage 拒绝(err=5)且
  PrintWindow 全黑(全黑是 UIPI 拒绝的典型特征)。
- **真最小化不可截**: 最小化时目标自停笔, capture_client 会自动 SW_RESTORE 重试。
- **完全离屏触发 DXGI 遮挡节流**: 截图变暗冻结, 在假数据上跑脚本 → 用
  `park_offscreen` 细缝停靠(留 2×30px 在屏内), 别把窗口挪出显示器。
- **后台键盘(windows 通道)**: 旧"WM_KEYDOWN 实测无效"结论系误传(当年从未实现过,
  只发过 WM_CHAR 打字)——带扫描码+状态位的 `key_tap` 已补(2026-10-31, selfcheck
  消息级验收)。消息只达"从消息循环读键"的窗口过程; 轮询型游戏免疫(见下条)。
  真机烟测 scripts/win_key_smoke.py(⚠ 目标提权时本进程必须同级, UipiError=权限墙
  不是"游戏无视")。华夏免费版=✅(2026-10-31 提权侧 W 开技能窗, 前后截图 15w 像素
  差验收; 普通权限被 UIPI 全拒, 见华夏条目)。中文输入仍走
  `type_text`(WM_CHAR)或 `paste`(剪贴板)。→ dx 通道(`input="dx"`)已可用:
  消息在目标进程内按大漠配方合成(焦点路由/手动 TranslateMessage/RIT 同步,
  2026-10-29 反编译 7.2319 组件对齐), 记事本 WM_GETTEXT 硬验收通过。
- **轮询型输入游戏(GetAsyncKeyState)对消息注入天然免疫**(kspoll 实测坐实:
  消息全送达但轮询读到 0)——解法 = `DxHost.hook_keystate()` 钩
  user32!GetKeyState/GetAsyncKeyState 喂虚拟按住态(大漠同机制, x86 专用),
  验收 scripts/dx_keystate_smoke.py; DirectInput 游戏还需 dinput8!GetDeviceState
  钩子(留位未实现)。
- **"鼠标能移动不能点击"= 点击处理有窗口激活门槛**(华夏世界画面定论, 2026-11-01):
  游戏非激活时忽略全部点击消息, 移动(hover)与键盘热键不受门槛 —— 所以"能移动、
  键盘好使、就是不能点"。**最小解 = `mouse="dx"` + `dm.EnableFakeActive(1)`**
  (假激活让游戏自认激活; 实测 8/8 走路, 光标锁/键态钩都不需要)。通用备胎仍留:
  `DxHost.mouse_lock()`(CMD_MOUSE_LOCK, 协议 v7, 钩 GetCursorPos 喂虚拟光标,
  大漠 dx.mouse.position.lock.api 同款)与 `DmSoft mouse="windows2"` —— 治的是
  另一种病(点击处理读真实光标坐标的), 华夏不是这种, 别个游戏可能用上。
- **判读尺陷阱(实测踩过)**: 自动判"点击有没有反应"别用全帧均值 diff ——
  大静态背景会把局部大变化稀释到 2-3(华夏软键盘面板开合 17k 像素, 均值才 2.9,
  一度全军误判"点击无效")。用**变化像素计数**(灰差>30 的像素数), 局部 UI 变化
  阈值 ~3000px; 双击 toggle 类动作会开+关抵消归零, 换单击验证。
- **华夏免费版私服(复古无锻造)四重墙实测(2026-10-31)**: ①客户端 32 位
  (*GodClass*, GDI 图色 OK); ②game.exe **自带进程护甲**(与[盾]登录器无关):
  DACL 拒 VM_READ/VM_OPERATION/CREATE_THREAD 但放行 0x400, 且**看门狗周期回挂**
  (解封后 ~35s 内被重新挂上)——OWNER WRITE_DAC 改写成 Everyone 全权可解, 但要
  **解封后立刻注入**(injector 已兼容); ③**模块隐藏**: LoadLibrary 明明成功
  (退出码=HMODULE)但模块枚举看不见 → 注入器 x86 用退出码当远程基址兜底;
  ④提权运行(HIGH IL, [盾]所赐), 普通权限宿主 PostMessage(WM_KEYDOWN)/
  PrintWindow/SetWindowPos 全被 UIPI 拒 err5 —— 宿主必须提权跑。
  **干净靶技巧**: 直启第二实例 `game.exe OABWLV`(抄登录器令牌, 普通权限) =
  中等 IL 无墙靶(消息/截图/注入全通, 适合做配方实验室); 千万别点登录界面
  「进入游戏」(记住的账号会把你在线实例顶下线)。
  **终验(2026-11-01, 世界画面在线实例)**: 键盘 windows 通道✅(W 13.1w 像素),
  鼠标 `mouse="dx"`+`EnableFakeActive(1)`✅(地面点击走路 24.4w 像素, DmSoft
  用户面 API 直测); 移动类操作点击前游戏必须在"自认激活"态, 否则全静默丢弃。
  验收脚本 scripts/huaxia_smoke.py(管理员终端 `uv run scripts/huaxia_smoke.py`)。
- **大漠键鼠反编译对照结论(2026-10-29, dm.dll 7.2642+组件 7.2319)**:
  GetCursorPos=虚拟光标客户区坐标(已对齐, 真人动鼠标不影响读数);
  KeyPress 修饰键 L/R→通用 VK 归一化+虚拟 Alt 追踪(后续键转 SYSKEY, 已对齐
  dmcore do_key); MoveTo 越界客户区拒绝(已对齐 DmSoft.MoveTo);
  **SendString 大漠真身是 WM_IME_CHAR(0x286)**(wParam=ANSI 字节大端打包,
  老 ANSI 窗口用), 我们 WM_CHAR UTF-16 —— 龙之谷打字不通时换这个配方;
  FindColor dir 1-7 扫描序未复刻(现只支持 0/取末位); 假激活深家族
  (SHOWWINDOW/WINDOWPOS*)是大漠功能哈希门控的选配模式, 未复刻(无条件过滤
  会破坏窗口管理)。
- **模拟器宿主窗口对合成消息免疫**(实测: 消息送达但无反应, 非 raw input、
  非激活门槛, 走自有输入通道)—— 模拟器后台输入一律用 adb(AdbTarget),
  别往 LDPlayerMainFrame/RenderWindow 打 win32 消息。
- **前台 SendInput 字母键被中文输入法吞**(实测 2026-10-29): IME 活动时注入的
  扫描码/VK 字母键进组合器, 投递的 WM_KEYDOWN 被改写 vk=0xE5, 永不产生
  WM_CHAR —— 打字一律 KEYEVENTF_UNICODE(VK_PACKET 直注字符, 布局/IME 双免疫,
  SendInputMouseKeyboard.type_text); 裸键(F 键/方向键等非文本键)不受 IME 影响,
  保留扫描码注入。另: GetGUIThreadInfo 失败时**静默返回假全零**(结构体不匹配
  会 FALSE, focus=0 是骗人的), 查焦点用 AttachThreadInput+GetFocus 才权威。
- **PostMessage 依赖目标从消息循环读输入**: DirectInput/RawInput 的游戏免疫,
  那种目标换前台 SendInput 通道(已实现: DmSoft mouse/keypad="normal",
  SendInputMouseKeyboard —— 动真光标单开专用; normal 图色=截屏裁客户区,
  坐标系仍统一客户区)。
- **d3d9 Present 转发必须真尾跳**(实测): Win10 d3d9!Present 检查调用来源,
  detour 里用 `call` 转发(栈上多一层外来帧/返址)第一次 Present 即崩
  (Eip 落栈页 DEP); `__attribute__((musttail))` 尾跳后栈布局与应用直调一致,
  捕获在跳前完成(dmcore.c our_present, 2026-10 会诊)。同病根预防: 给目标
  进程挂任何"转发壳"都要想一下栈上会不会多出外来帧。
- **dx 重绑语义**(实测 2026-10-30): unbind 后 DLL 常驻, 设备 vtable 钩留着但被
  detached 闸住纯转发(零 g_hdr 解引用); 重绑时注入器按确定性副本名(size+mtime)
  扫到驻留模块就直接远程 DxInit 复用, 在世设备无缝续捕。重编译后指纹名变才会
  拷新副本 —— 此时在世设备要等游戏重建设备才有图色(开发者自知)。教训: 旧的
  "BYE 全还原钩子"方案下重绑 hook_d3d9 假成功(rc=0 但永无帧), 静默死局。
- **DPI**: 用前 `ensure_dpi_aware()`, 非 100% 缩放下坐标=物理像素。
- **自建注入器会被 Defender 拦**(injector64.exe 反复被隔离; injector32 未被标记):
  dx 通道在 64 位目标上使用前, 需管理员加一次排除
  `Add-MpPreference -ExclusionPath <repo>\imgbot\dx\bin`(仅产物目录)。
- **模块隐藏的边界**(dx.hide_module): 摘 PEB 三链 → 模块枚举/扫链不可见;
  按名 `GetModuleHandle` 走 LdrpHashTable 仍可解析(摘哈希表需扫 ntdll 未导出
  全局, 不做)。反作弊扫模块列表的场景已够用。

## Agent skills

### Issue tracker

本地 markdown 工单: `.scratch/<feature>/`(一特性一目录, spec.md + issues/NN-slug.md)。See `docs/agents/issue-tracker.md`.

### Triage labels

默认五标签(needs-triage/needs-info/ready-for-agent/ready-for-human/wontfix), 记为工单文件的 `Status:` 行。See `docs/agents/triage-labels.md`.

### Domain docs

单上下文: 根 GLOSSARY.md(潜影等术语)+ docs/adr/(0001-dm-dx-channel)。See `docs/agents/domain.md`.
