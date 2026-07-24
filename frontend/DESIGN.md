# BidPilot DESIGN.md

> BidPilot AI Project Space — Open Design `frontend-design` brand contract (v2).

## Direction

现代、轻盈、安静的 AI 投标协作空间。取消常驻侧栏；悬浮顶栏 + 内容空间 + 命令面板。

| Dial | Value |
| --- | --- |
| VARIANCE | 4 |
| MOTION | 5 |
| DENSITY | 5 |

**Memorable quality：** 打开后像进入项目空间，而不是后台管理系统。

## Color

| Role | Token | Value |
| --- | --- | --- |
| Canvas | `--bp-bg` | `#F7F8FA` |
| Surface glass | `--bp-surface` | `rgba(255,255,255,0.84)` |
| Surface solid | `--bp-surface-1` | `#FFFFFF` |
| Text | `--bp-text` | `#15171A` |
| Secondary | `--bp-text-muted` | `#626871` |
| Tertiary | `--bp-text-faint` | `#9298A1` |
| Border | `--bp-border` | `rgba(20,24,31,0.09)` |
| Border strong | `--bp-border-strong` | `rgba(20,24,31,0.16)` |
| Accent | `--bp-primary` | `#4F64E8` |
| Accent hover | `--bp-primary-hover` | `#4054D3` |
| Soft | `--bp-primary-soft` | `#EEF0FF` |
| Success / warn / danger | | `#258765` / `#C47A20` / `#C94B53` |
| Focus | `--bp-focus` | `rgba(79,100,232,0.28)` |

## Typography

- UI: `"Manrope", "Noto Sans SC", system-ui, sans-serif`
- Brand mark: Manrope 600
- Mono (rare): IBM Plex Mono

## Layout

- No persistent sidebar
- Floating top nav 64–72px, inset 16–24px, blur glass
- Content max-width ~1180px centered on home; project workspace can go full-bleed
- Command palette ⌘K; AI float entry bottom-right

## Anti-slop

No cyber neon, no dark mesh, no permanent dark rail, no card-in-card walls, no step/dev copy on product surfaces.
