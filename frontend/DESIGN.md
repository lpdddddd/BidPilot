# BidPilot DESIGN.md

> BidPilot Liquid Intelligence — Open Design brand contract (v2.1 glass polish).
> Layout frozen from AI Project Space (`f9b6e3f` / `2f0a533` revert base).

## Direction

在既有顶部导航 + 项目空间信息架构上，仅升级材质为液态玻璃：通透、精密、环境光响应。

| Dial | Value |
| --- | --- |
| VARIANCE | 3 |
| MOTION | 4 |
| DENSITY | 5 |

**Memorable quality：** 玻璃边沿折射与指针高光，而不是霓虹或卡片堆砌。

## Color / Glass

| Role | Token | Value |
| --- | --- | --- |
| Canvas | `--bp-canvas` | `#EEF2F7` |
| Nav glass | `--bp-glass-nav` | `rgba(248,251,255,0.68)` |
| Primary glass | `--bp-glass-primary` | `rgba(255,255,255,0.62)` |
| Text | `--bp-text` | `#172033` |
| Accent | `--bp-primary` | `#3977F6` |
| Cyan soft | `--bp-cyan-soft` | `rgba(73,204,232,0.12)` |
| Blur | `--bp-glass-blur` | `24px` |

## Frozen layout

不得改动 DOM 层级、栅格、max-width、卡片轨道宽度、模块顺序与业务逻辑。材质改动仅限颜色、透明、阴影、blur、伪元素与 transition。

## Anti-slop

无赛博霓虹、无大面积紫蓝发光、无点阵网格、无呼吸灯循环、无影响可读性的过低透明度。
