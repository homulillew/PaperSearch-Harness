# 专业研究报告构造指南

> 版本：v0.4
> 适用角色：Report Constructor
> 目标：把已接受的 Research State 构造成保真、可导航的 Report Brief

## 1. 权责边界

Constructor 负责选择材料、设计读者认知路径、建立认知分组，并判断哪些分组值得成为读者可见的导航节点。它决定 section 顺序、父子归属和 `outline_depth`；Writer 只负责忠实实现这些裁决。

核心原则：

> **结构不是排版，而是交付语义。**

> **认知步骤不等于导航节点。**

> **Research taxonomy 不自动成为 Report taxonomy。**

Research Contract 决定报告必须回答什么；Report Brief 决定怎样组织这些答案。“覆盖主要技术路线”默认是内容要求，除非用户明确要求布局，不应自动变成“按技术路线逐章组织”。

## 2. 从认知路径到可见层级

先确定读者完成报告后应形成的总体认识，再安排建立这一认识所需的背景、机制、比较和闭合顺序。只有当一个认知分组值得专业读者之后扫描、回访、比较或定位时，才把它设为 section 导航节点。

`ReportBrief.sections` 是按阅读顺序排列的扁平树表示：

```text
outline_depth 0 → Markdown H2
outline_depth 1 → Markdown H3
outline_depth 2 → Markdown H4
```

首个 section 必须为 depth 0，向下最多逐级进入，向上可以回到任意已有层级。深度表达语义归属，不表达重要性评分。

`ReportBriefSection.title` 是最终 reader-visible heading 的正式文本，不是内部 label。Constructor 提交 Brief 前必须同时判断标题是否专业、自然、有导航价值，并适合作为最终成品标题；Writer 不负责重新命名。所有报告标题和 section heading 使用 ATX syntax，`outline_depth` 对应 ATX H2+。

## 3. 专业技术调研的宏观体裁

强默认是：

```text
摘要
领域概览
主体综合
横向比较（适用时）
开放问题（适用时）
结论
```

以下部分按任务条件选用：

```text
最新进展与趋势
研究范围与证据说明
方法说明
```

这些是 semantic defaults，不是固定模板。标题名称、数量和深度应服从当前问题、目标读者和认知路径；不适用的槽位可通过既有 `intentional_omissions` 说明。

## 4. 防止欠结构化与过度标题化

不要把需要回访的重要机制或比较全部压进长段落和粗体段首。也不要机械地把以下内容升级为 heading：

- 一篇论文；
- 一个实验；
- `argument_flow` 中的每一步；
- Research State 中每个既有分类。

标题树应反映真实认知分组，而不是材料清单。多个代表工作服务于同一判断时，应在共同的认知节点内综合，而不是形成“一篇论文一个 H4”。

## 5. Semantic Ceiling

Constructor 可以从已接受材料中恢复建立连续理解所必需的：

- 背景；
- 机制解释；
- 概念桥；
- 代表例子；
- 比较条件；
- 规模校准。

这些恢复不得形成 Research State 尚未接受的新共识、更强泛化、新路线关系、新开放问题或面向 Contract 的新研究判断。若缺失内容需要新的研究裁决，应请求具有 Research Authority 的阶段确认，而不是在 Brief 中补造。

## 6. 交付前检查

提交 Report Brief 前确认：

1. sections 已覆盖交付要求，但没有把 Contract 的内容要求机械改写成章节布局；
2. 顺序形成连续的认知路径；
3. 每个 `section.title` 都专业、自然，并作为最终 heading 具有真实导航价值；
4. `outline_depth` 准确表达各 section 的父子归属且没有跳级；
5. 重要比较、开放问题和结论在适用时可被快速定位；
6. Brief 没有越过已接受 Research semantics 的上限。

若这些条件不能同时成立，先重构 Brief；不要把结构裁决留给 Writer 猜测。
