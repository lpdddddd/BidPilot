# BidPilot DESIGN.md

> BidPilot Quiet Intelligence — Open Design `frontend-design` brand contract (v3).

## Direction

安静、清晰、精确的投标协作界面。保留顶部导航与项目空间信息架构；拒绝 AI SaaS 装饰与后台模板感。

| Dial | Value |
| --- | --- |
| VARIANCE | 2 |
| MOTION | 4 |
| DENSITY | 5 |

**Memorable quality：** 像一套克制的系统应用——干净中性色、系统蓝强调、大表面少卡片、操作时才出现控件。

## Color

| Role | Token | Value |
| --- | --- | --- |
| Canvas | `--bp-canvas` / `--bp-bg` | `#F5F5F7` |
| Elevated | `--bp-canvas-elevated` | `#FBFBFD` |
| Surface | `--bp-surface` / `--bp-surface-1` | `#FFFFFF` |
| Subtle glass | `--bp-surface-subtle` | `rgba(255,255,255,0.72)` |
| Hover | `--bp-surface-hover` | `rgba(0,0,0,0.035)` |
| Text | `--bp-text` / `--bp-text-primary` | `#1D1D1F` |
| Secondary | `--bp-text-muted` | `#6E6E73` |
| Tertiary | `--bp-text-faint` | `#86868B` |
| Accent | `--bp-primary` / `--bp-accent` | `#0071E3` |
| Accent hover | `--bp-primary-hover` | `#0077ED` |
| Soft | `--bp-primary-soft` | `rgba(0,113,227,0.09)` |
| Success / warn / danger | | `#248A3D` / `#B86600` / `#D70015` |
| Separator | `--bp-border` | `rgba(0,0,0,0.08)` |

## Typography

系统字体栈优先（无 Google Fonts）：

`-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif`

## Layout

- 无常驻侧栏
- 顶栏与内容统一 `max-width: 1120px`
- 顶栏：轻分隔 + 轻模糊，不是厚胶囊卡片墙
- 首页唯一强焦点：当前焦点大表面；其余降权

## Anti-slop

无点阵/网格、无装饰光球、无蓝紫霓虹渐变、无大面积玻璃、无呼吸光循环、无假 Mac 窗口。
