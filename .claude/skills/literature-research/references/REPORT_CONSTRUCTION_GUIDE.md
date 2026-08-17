# Report Construction Guide

> 版本：v0.6
> 适用角色：Report Constructor / Editorial Design Authority
> 目标：把已接受的研究语义编译为可由 Authoring 实现的 Lean Report Brief

Constructor 不写正文，也不重新研究。它只拥有报告的编辑设计权。
`Report Brief` 仍是唯一报告语义中间层，但 v0.6 将它收缩为五个字段。

## 1. 输入边界

默认输入是 `ReportConstructionInput`：

```text
ReportConstructionContext
+
optional BriefRepairContext
```

`ReportConstructionContext` 只暴露 Contract、accepted approach semantics、
Findings、Open Problems、未解决的 Delivery-relevant gaps 与 stable semantic refs。
它不默认暴露 paper inventory、representative-paper refs、authors、DOI、canonical
URL 或 raw evidence locators。需要细节时，先声明信息需要，再用
`delivery-inspect` / `delivery-read-source` 按 stable ref 定向下钻。

## 2. Lean Report Brief

Brief 只表达五个字段：

```text
audience   目标读者是谁、带着什么前置知识
promise    这篇报告向读者承诺建立什么认识
frame      全文采用的稳定分析框架（比较坐标 / 分类依据）
arc[]      认知推进的有序阶段（读者依次经过的主要认识台阶）
focus[]    本篇主动聚焦的范围边界（不写什么、不比较什么）
```

`arc` 与 `focus` 是非空字符串元组。它们是编辑意图的声明，不是段落计划、
不是 heading 文本、不是 semantic move 列表，也不带 outline depth。
Constructor 不再决定 heading 文本、heading 层级、section 顺序或父子归属——
那些属于 Authoring 的正文实现权。Constructor 只决定读者要形成什么认识、
按什么顺序、在什么范围内。

## 3. BRIEF repair

repair mode 读取 previous Brief，以及每项 `problem`、`resolution_condition`
和 optional `location`。反馈只规定修复后必须成立什么，不规定具体 heading、
新 section 数量或固定文本。默认保留没有被反馈推翻的有效设计，执行 minimal
sufficient reconstruction。如果 accepted research semantics 足以满足条件，重建
Brief；如果不形成新的 contract-facing research judgment 就无法满足条件，
Constructor 才升级到 Research。

## 4. 权限边界

Constructor 可以恢复 accepted semantics 的解释密度，但不能创造新 consensus、
更强 generalization、新 Approach relationship、新 Open Problem 或新的研究判断。
它不写正文，不替 Authoring 决定段落、句子、过渡、prose/list/table、heading
文本或局部节奏。

最终原则：

> 先设计读者必须形成的最小心智模型，再声明足以送达它的编辑意图。
