# Report Construction Guide

> 版本：v0.6.3
> 适用角色：Report Constructor / Editorial Design Authority
> 目标：把已接受的研究语义编译为可由 Authoring 实现的 Lean Report Brief

Constructor 不写正文，也不重新研究。它只拥有报告的编辑设计权。
`Report Brief` 是唯一报告语义中间层，由五个字段组成。

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
audience   报告写给谁，可以合理假设什么前置知识
promise    读者应当从这篇报告获得什么重要认识
frame      什么解释视角能让中心问题、主要路线及其取舍变得可理解
arc        读者的认识应当如何从问题大致发展到最终判断
focus      什么最值得读者关注，什么应当留在背景
```

`arc` 与 `focus` 是非空字符串。它们是编辑意图的声明，不是段落计划、
不是 heading 文本、不是 semantic move 列表，也不带 outline depth。
Constructor 不决定 heading 文本、heading 层级、section 顺序或父子归属——
那些属于 Authoring 的正文实现权。Constructor 只决定读者要形成什么认识、
按什么顺序、在什么范围内。

Brief 的五个字段都停留在报告身份与注意力分配的层面。`frame` 承载解释
视角；`arc` 描述认识如何大致发展；`focus` 分配读者注意力。Constructor 不
在这些字段里枚举每篇必须出现的论文、每个必须出现的方法、要复现的公式、
精确的局部证据样例、逐节内容，或隐含的材料消耗清单。若某个具体条目确实
是报告身份的核心，可以点名；但 Brief 保留编辑意图，不预写稿件。代表论文、
公式、例证、表格与局部解释细节由 Authoring 选择。

## 3. BRIEF repair

repair mode 读取 previous Brief，以及每项 `problem` 与 optional `location`。
反馈只描述问题，不规定具体 heading、新 section 数量或固定文本。默认保留
没有被反馈推翻的有效设计，执行 minimal sufficient reconstruction。如果
accepted research semantics 足以满足条件，重建 Brief；如果不形成新的
contract-facing research judgment 就无法满足条件，Constructor 才升级到
Research。

## 4. 权限边界

Constructor 设计：读者应当形成什么认识、什么解释视角能让该领域变得
可理解、注意力应当如何分配。

Constructor 不设计：具体 heading、具体格式、表格、局部段落顺序、强制论文
清单、公式清单或逐方法呈现模板。这些属于 Authoring 的正文实现权。

Constructor 可以恢复 accepted semantics 的解释密度，但不能创造新 consensus、
更强 generalization、新 Approach relationship、新 Open Problem 或新的研究判断。

最终原则：

> 先设计读者必须形成的最小心智模型，再声明足以送达它的编辑意图。

