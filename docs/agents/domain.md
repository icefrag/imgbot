# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`GLOSSARY.md`** at the repo root(本仓已有: 潜影/DmSoft壳/Target双后端/通道/假激活等术语, 含 Avoid 条目)
- **`docs/adr/`**: read ADRs that touch the area you're about to work in.(本仓已有 0001-dm-dx-channel: 潜影注入通道的决策与边界)

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

## File structure

Single-context repo:

```
/
├── GLOSSARY.md
├── docs/adr/
│   └── 0001-dm-dx-channel.md
├── imgbot/                          # 库本体(单包, 无 monorepo 拆分)
└── scripts/
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `GLOSSARY.md`. Don't drift to synonyms the glossary explicitly avoids.

要点提醒(详见 GLOSSARY.md):
- 自研后台插件叫**潜影(qianying)**, 禁止用"大漠"指代本库组件("大漠"仅指反编译参考的原版商业插件)
- 通道序一律 BGR; 坐标=绑定窗口客户区
- Target 是操作对象(分 AdbTarget/Win32Target), "绑定"是一次建立持续操作关系的行为

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0001 (潜影注入通道范围决策), but worth reopening because…_
