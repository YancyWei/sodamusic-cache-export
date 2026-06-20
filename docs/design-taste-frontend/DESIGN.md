# SodaMusic Cache Export — 前端设计品味审查与优化计划

> 审查日期：2026-06-20  
> 范围：`web/` 目录下全部前端源码（Next.js 15 + React 19 + Tailwind v4 + Framer Motion）  
> 目标输出：可直接指导小步高质量迭代的优先级计划  
> 放置位置：`docs/design-taste-frontend/DESIGN.md`（按用户要求）

---

## 1. 执行摘要

当前前端已能满足基本功能：状态卡片、导出配置、缓存列表浏览、目标搜索、任务进度、日志展开。技术栈现代（Next 15 静态导出 + Geist + Phosphor + Tailwind 4 CSS-first + Framer Motion），部署零依赖（Python 服务器直接 serve `src/web`）。

**当前前端评分（按 design-taste-frontend 标准）：7.4 / 10**

**核心差距（不是功能，而是“品味”与工程质量）：**
- 组件架构尚可，但状态提升过度 + 内部组件定义过多，导致复用与测试困难。
- 动画大量依赖 Framer 的 height:auto 展开，违背“仅用 transform/opacity 实现硬件加速”的黄金法则，容易出现布局抖动（jank）。
- 视觉设计基本可用，但缺乏严格的度量驱动（metric-based）体系：间距、圆角、字体阶梯、焦点态、对比度均存在不一致。
- 可访问性与键盘体验处于“能用”水平，缺少 ARIA、live region、roving focus 等桌面工具应有的专业度。
- 轮询策略简单粗暴，性能与电量（本地虽不关键，但仍属坏味道）有优化空间。
- 设计令牌与硬编码 class 混用，Tailwind v4 的优势未完全发挥。

**预期改进后目标：9.0+ / 10**（达到本地专业桌面工具的水准，动画丝滑、状态清晰、键盘友好、可维护性强）。

**约束（必须遵守 AGENTS.md）：**
- 保持静态导出 + Python 薄服务器架构，不引入新 bundler / 框架。
- 改动保持小而集中，避免顺手大重构。
- 能复用现有模式（clsx + cn、现有 ui 基元、useInterval、Card/Button 模式）。
- 完成后必须能通过 `npm run build:web` 且手动验证主要流程。
- 不要为“未来”增加抽象。

---

## 2. 现状架构速览

```
web/
├── app/
│   ├── layout.tsx          (Geist + 基础 html)
│   ├── page.tsx            (极简壳)
│   ├── ClientDashboard.tsx (巨型状态容器 + 编排)
│   ├── globals.css         (@theme + 少量 base)
│   ├── components/
│   │   ├── EmptyState.tsx
│   │   └── ui/ (Button, Card, Badge, Input, Select)
│   └── sections/ (7 个垂直领域组件)
├── hooks/ (5 个自定义 hook，轮询驱动)
├── lib/ (api, constants, format, utils)
└── 构建：next build -> dist -> copy-to-src-web -> src/web/
```

关键数据流：
- `usePreflight(6000)` 持续轮询 `/api/preflight-status`
- `useSources` 手动触发 + 本地 filter/sort
- `useJob(1200)` 仅 running 时轮询
- `useTargetSearch` 按需搜索
- 大量状态（dryRun / format / mp3Bitrate / overwrite ...）全部提升到 ClientDashboard

---

## 3. 按 design-taste-frontend 维度审查

### 3.1 严格组件架构（Strict Component Architecture）

**现状问题：**
- `ClientDashboard.tsx:27-267` 承担了“全局配置状态 + 所有事件处理 + 条件渲染决策”，~240 行，违反单一职责。
- `SourceList.tsx` 内定义 `SourceItem`（~90 行）、`TargetSearch.tsx` 内定义 `TargetMatchCard`、`SettingsPanel.tsx` 内定义 `Checkbox` —— 这些本应是可独立测试/复用的组件。
- 过滤逻辑、排序逻辑、候选版本切换逻辑散落在 hook + section 之间，重复计算。
- `ActionCenter` 只是两个 Card 的切换器，命名与职责不匹配（更像 ModeSwitch 或 ViewToggle）。
- 无 Context 或精细化状态切片，导致每次微调都要透传 8~15 个 props。

**机会：**
- 提取 `ExportConfigContext` 或使用更轻量的 `useReducer` + 派生状态（colocate）。
- 将列表项、匹配卡片、复选框提升为正式 ui/ 或 sections/ 子组件。
- 引入 `useMemoizedFilter` 或把 filter 逻辑彻底留在 `useSources`（已部分完成），dashboard 只负责“意图”。

**度量目标：** 最大组件 < 120 行；props 接口清晰（<=8 个）；无匿名函数组件定义在父组件内。

### 3.2 CSS 硬件加速与动画质量（CSS Hardware Acceleration）

**现状问题（最严重的设计品味问题）：**
- 大量使用 `motion.div` + `animate={{ height: "auto" }}` + `AnimatePresence`（SourceList、TargetSearch、SettingsPanel、JobProgress）。
  - Framer height:auto 会强制布局计算，不是 60fps 硬件加速路径。
  - 常见于折叠面板时出现内容跳动或卡顿。
- 进度条使用 `width: xxx%` + spring transition（JobProgress:81-86），虽然视觉可接受，但 width 动画会触发 layout。
- 几乎所有 motion 都只为了“淡入 + 高度展开”，性价比极低（framer-motion 本身体积不小）。
- 无 `will-change`、`transform` 兜底、无 `prefers-reduced-motion` 尊重。
- 部分 hover/focus 只有 `transition-colors`，缺少微交互一致性。

**推荐原则（必须写入计划）：**
- 仅允许 `opacity` + `transform`（translate/scale/rotate）进入 motion。
- 高度展开改用：
  - CSS `max-height` + `transition` + 内容自适应（或 grid 技巧）。
  - 或 `layout` 仅用于真正需要 FLIP 的场景。
- 进度条优先用 `<progress>` 或 `transform: scaleX` + `transform-origin: left`。
- 引入 `ReducedMotion` 包装或全局 config。
- 考虑渐进：简单展开用纯 CSS + React state，保留 Framer 仅用于 JobProgress 关键进度条。

**当前使用统计（grep）：** 4 个文件、约 15 处 motion/AnimatePresence，90% 是高度动画。

### 3.3 度量驱动设计（Metric-based Rules）

**缺失的度量：**
- 视觉节奏：间距 scale（当前 p-3/p-4/p-5/p-6 + gap-2/3/4 随意组合）。
- 圆角体系：rounded-xl / rounded-2xl 混用，Card 用 2xl，其他按钮用 xl。
- 字体阶梯与行高未在 @theme 完全定义（globals.css 只定义了 surface/ink/accent 等）。
- 对比度：zinc-400 / zinc-500 在浅色背景上部分场景接近 4.5:1 边缘。
- 触控/点击目标：很多小按钮（size=sm）+ 图标，实际可点击区域 < 40px。
- 动画时长/缓动：硬编码 spring stiffness 120 / 默认 transition-colors，未标准化。
- 空状态：`EmptyState` 只是一个灰色圆 + 文字，看起来像占位符而非设计完成品（无插图、无建议动作）。

**tailwind.config.ts 与 globals.css 冲突：**
- Tailwind 4 推荐把几乎所有主题移到 CSS `@theme`，但 tailwind.config 仍定义 `surface`、`panel`、`fontFamily`、`borderRadius.4xl`。
- 结果是部分地方用 `bg-surface`（未在 class 大量出现），多数仍用 `bg-white` / `bg-zinc-50` 硬写。

### 3.4 可访问性与键盘体验（Accessibility & Keyboard）

- ActionCenter 的两个模式卡片是用 `Card + onClick` 实现的“按钮”，缺少 `role="button"`、`aria-pressed`、键盘 Enter/Space 一致处理（虽 Card 可能透传，但语义不对）。
- Select 与 Input 的 focus ring 只在 focus-visible 时出现，部分场景（尤其是列表内）不够明显。
- JobProgress 的日志 pre 无 `aria-live` 或 `role="log"`，用户看不到“新日志追加”提示。
- 整个应用缺少：
  - 全局快捷键（⌘/Ctrl + R 刷新、⌘K 搜索等）。
  - 焦点管理（任务开始后焦点应移到进度区）。
  - 屏幕阅读器友好标签（很多 icon-only 按钮只有 title，没有 aria-label）。
- 颜色仅靠色块区分（success/warning），缺少图案或文字辅助（虽有 Badge 文字，但仍需改进）。

### 3.5 性能与数据流

- usePreflight 永远 6s 轮询，即使页面不可见、即使没有变化。无 `document.visibilityState` 优化。
- useJob 1200ms 在任务进行中会持续 setState + fetch，导致列表区也重渲染（虽小）。
- SourceList filtered 是 useMemo，但每次 rows/filter 变化都要全量 sort + filter（小数据量可接受，但无虚拟化）。
- 每次 preflight 返回新对象引用 → runtime useMemo 依赖变化 → 所有子组件可能重渲染。
- 没有 request dedup / abort controller。
- buildExportPayload / handleStartTarget 依赖数组很长，容易出错。

### 3.6 其他工程问题

- 过滤器状态（SourceFilter）定义了 `sourceFormat`、`selected` 模式，但 UI 只暴露了部分能力（“未选择时导出全部”文字说明不明显）。
- 目标搜索无防抖（输入后需点击按钮或回车），体验稍滞。
- 没有任务完成后的“清空”或“一键导出下一个”的明显 CTA。
- 构建产物复制脚本简单粗暴（rm + cp），无增量。
- 缺少前端 lint/typecheck 在 CI 中的显式验证（虽 Python 测试覆盖后端）。
- EmptyState 插图缺失让“专业感”掉档。

---

## 4. 优先级行动计划

| 优先级 | 编号 | 标题 | 主要文件 | 预计工作量 | 风险 | 验证要点 |
|--------|------|------|----------|------------|------|----------|
| P1 | F1 | 替换高度动画为硬件加速实现 | JobProgress, SourceList, TargetSearch, SettingsPanel | 2-3h | 低 | 展开/收起无 jank，prefers-reduced-motion 生效，性能面板 60fps |
| P1 | F2 | 拆分 ClientDashboard 状态与组件 | ClientDashboard.tsx + 新建 ConfigContext / 组件 | 2h | 中 | dashboard < 150 行；所有 section props ≤ 8 个；手动流程不变 |
| P1 | F3 | 建立统一设计令牌与视觉体系 | globals.css, tailwind.config.ts, ui/*, Card/Button | 1.5h | 低 | 所有圆角/间距/颜色来自 @theme；视觉回归一致 |
| P2 | F4 | 提升可访问性（ARIA、键盘、焦点） | 所有 sections + ui + ClientDashboard | 2h | 低 | axe / 键盘 Tab 全流程；aria-pressed、role、live region |
| P2 | F5 | 轮询策略优化 + 可见性感知 | usePreflight.ts, useJob.ts, useSources | 1h | 低 | 后台标签页轮询降频或暂停；任务中保持 1.2s |
| P2 | F6 | 空状态与微交互打磨 | EmptyState.tsx, StatusCard, ActionCenter | 1h | 低 | EmptyState 提供视觉重量与建议动作；hover/active 一致 |
| P2 | F7 | 精简 Framer Motion 使用 | 仅保留关键进度条，其余换 CSS | 0.5h | 低 | bundle 分析 framer 体积下降或 tree-shake 更干净 |
| P3 | F8 | SourceList 虚拟化或分页（若数据量大） | SourceList.tsx | 1.5h | 中 | 先度量真实缓存条数，>200 条再做 |
| P3 | F9 | 增加全局快捷键与完成态 CTA | ClientDashboard + JobProgress | 1h | 低 | ⌘R 刷新、Esc 清任务等 |
| P3 | F10 | 文档化前端贡献规范 | docs/design-taste-frontend/CONTRIBUTING.md | 30min | 无 | 新增或修改组件必须遵循的 checklist |

**总预计：约 12-15 小时高质量小步改动。**

建议执行顺序：F1 → F3 → F2 → F4/F5 并行 → F6/F7 → P3。

---

## 5. 具体设计原则（写入代码即规范）

1. **动画黄金法则**  
   `transform` 与 `opacity` 是唯一允许用于性能关键动画的属性。高度变化用 `max-height` + `transition-[max-height]` 或 `grid-rows` 技巧。

2. **组件原子化**  
   - ui/ 目录只放无状态纯展示基元（Button/Card/Input/Select/Badge/EmptyState）。
   - 带业务逻辑的列表项、卡片放入 sections/。
   - 任何组件内不再定义其他组件（除极小私有辅助）。

3. **令牌优先**  
   所有颜色、间距（gap-、p-、m-）、圆角、阴影、过渡时长必须来自 `@theme` 或 Tailwind 语义 class。globals.css 成为单一真相源。

4. **可访问性基线**  
   - 所有可点击元素必须可键盘聚焦且有可见焦点指示。
   - Icon-only 按钮必须有 `aria-label` 或 `title` + 文本回退。
   - 状态变化使用 `aria-live` 或 `role="status"`。

5. **性能预算（本地工具也可执行）**  
   - 空闲时轮询间隔 ≥ 8s 或暂停。
   - 列表渲染使用 `useMemo` + 稳定 key。
   - 避免在 render 期间创建新函数传给 motion（用 useCallback）。

6. **状态管理原则**  
   - 全局配置（format、bitrate、flags）使用 Context + reducer 或 Zustand-lite（不加新依赖则用 Context）。
   - 列表数据与 UI 筛选状态分离（已在 useSources 部分实践）。

7. **空状态与反馈**  
   EmptyState 必须包含语义化插图（可用 Phosphor 大图标或简单 SVG）、标题、描述 + 推荐下一步行动。

---

## 6. 验证与交付 checklist

每次迭代后必须执行：
- `cd web && npm run build:web` 成功，产物复制到 `src/web`。
- 启动 Python 服务 `python3 src/start_sodamusic_export.py`，手动走完：
  1. 预检 → 状态卡片更新
  2. 切换浏览/搜索模式（展开收起）
  3. SourceList 搜索、筛选、版本切换、选择导出
  4. 启动导出 → JobProgress 出现 + 进度条动画 + 日志展开
  5. TargetSearch 搜索 + 选择 + 启动
  6. 键盘 Tab 全流程 + Enter 操作
- 观察 Chrome Performance / React DevTools Profiler，展开动画期间无长任务。
- 无 console 错误、无 layout shift 肉眼可见。
- 至少跑一次 `python3 -m pytest tests/`（确认后端未受前端静态变更影响）。

推荐在 PR 中附上：
- 前后 bundle 大小对比（`du -sh src/web`）。
- 关键动画前后的 Performance 录屏或 trace。

---

## 7. 不做的事（边界）

- 不引入新状态库、CSS-in-JS、新的 UI 框架。
- 不做全站暗色模式（除非用户显式要求）。
- 不重写整个 dashboard 为单个文件（保持现有 sections 拆分）。
- 不给 SourceList 加虚拟滚动，除非真实数据证明需要（先加计数 telemetry 或手动检查）。
- 不修改 Python 服务器除非 API 必须调整（保持薄壳）。
- 动画库简化以“移除不必要 motion”为准，不彻底移除 framer-motion（保留给未来真正需要的 FLIP/手势）。

---

## 8. 后续演进建议（P4 以后）

- 前端单元测试（Vitest + React Testing Library）覆盖关键 hook 与 sections（目前几乎为 0）。
- 把 preflight / source 数据结构生成 TypeScript 类型（从 Python 生成或手动维护严格版）。
- 考虑把 Job 日志渲染改成虚拟列表（长日志场景）。
- 增加“最近导出”历史区（使用 localStorage 或从 manifest 读）。
- 当用户真正需要更复杂筛选时，再引入更强大的表格组件。

---

## 9. 总结

这个前端目前是“能跑的内部工具 UI”。要达到 **design-taste-frontend** 所要求的专业水准，需要在**动画硬件加速、组件严格拆分、令牌化视觉体系、可访问性**四个方向做有针对性的小步改进。

优先做 F1（动画）、F3（设计令牌）、F2（状态架构），这三项改动对用户感知提升最大、风险可控。

本计划完成后，前端应能让人感受到“这个本地工具是认真设计的”，而不仅仅是“功能能用”。

---

**附：关键参考文件行号（审查时使用）**

- `web/app/ClientDashboard.tsx:74-162`（payload 构建与大量 state）
- `web/app/sections/SourceList.tsx:119-165`（列表 + 动画 + 内部组件）
- `web/app/sections/JobProgress.tsx:80-87`（进度条 width 动画）
- `web/hooks/usePreflight.ts:36-38`（无条件轮询）
- `web/app/globals.css:1-38`（当前 @theme 规模）
- `web/app/components/ui/*.tsx`（基元一致性检查点）
- `web/next.config.ts:4-11`（静态导出配置正确）

计划完成即代表前端可以进入“设计品味驱动的持续演进”阶段。