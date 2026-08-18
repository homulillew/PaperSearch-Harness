# Report Review Guide

> 版本：v0.6.2
> 适用角色：Fresh Reader / Reader Acceptance Gate
> 目标：验证最终稿是否真正作为面向目标读者的认知载体成立
> 不负责：研究完成性判断、Research State 修改、Primary Evidence 最终核验、事实修复

Fresh Reader 回答的是：

> **这篇报告作为文章是否成立？目标读者能否仅凭正文形成连续、稳定、可复述的
> 领域理解？**

它不回答研究判断最终是否被 Research State 与 Primary Evidence 支持——后者属于
Research Integrity Review。Reader Gate 与 Research Integrity Gate 必须保持独立。

Reviewer 不是“编辑老师”，也不是“风格打分器”。它问的是：作为第一次读者，
我实际建立了什么理解？哪里无法继续建立理解？为什么？本 Guide 不使用统一
质量分数。

## 两阶段冷读

每个新 Manuscript 必须由新的 Reviewer instance 审查。任何 Manuscript 修改
都会使旧 Reader PASS 失效。两阶段不能倒置：如果一开始就读取 Brief，Reviewer
会知道作者意图并自动替文章补全缺失逻辑，削弱 first-time-reader validation。

```text
Phase 1 — Blind Read：不看 Brief，重建读者实际获得的认知结构
Phase 2 — Brief Check：读取 Brief，比较设计认知路径与实际认知路径
```

## Phase 1 — Blind Read

Phase 1 只接收：Deliverable description、Audience、Report Review Guide、
Manuscript（rendered Reader Surface）。Phase 1 不接收 Report Writing Guide，
也不接收 Report Brief、Research State、Paper Analysis、Approach Family、
Finding、Open Problem 或前一轮 Reviewer 的推理记录。`Audience` 只暴露目标
读者描述本身，不暴露 Brief 的 `promise` / `frame` / `arc` / `focus`。

Reviewer 应模拟真实专业读者，而不是逐句寻找问题。正常从头到尾阅读；不提前
构造修订方案；不因为“猜到了作者想表达什么”而自动补全缺失关系。Reviewer 能
推断出某个缺失关系，不代表文章已经把该关系传递给读者——不得用 Reviewer 自身
知识替正文补桥。

完成阅读后，Reviewer 记录实际形成的整体理解。只报告**实质阻止报告作为
成品专业文章成立**，或**实质阻止在合理阅读成本下形成清晰高层理解**的问题。
孤立的措辞偏好、单句可优化或纯审美差异不构成 blocker。Reviewer 不做
checklist 式质量审计，也不对文章做逆向分析。

Phase 1 只诊断，不归因：只回答哪里发生了真实理解失败或足以阻止专业成品成立
的系统性缺陷，不在这一阶段决定 `MANUSCRIPT` / `BRIEF` 修复目标。先诊断，
再归因。

## Phase 2 — Brief Check

Phase 2 在 Blind Read 记录冻结后才接收 `Report Brief` 与 `Contract`。Phase 2
只接收：冻结的 Blind Read 结果、Report Brief、Contract、Report Review
Guide——**不接收 Manuscript，不接收 reader surface**。Phase 1 与 Phase 2
使用各自新鲜的 Reviewer instance；唯一的桥梁是冻结的 Blind Read 结果。
Contract 让 Phase 2 能发现内部自洽但遗漏了 Contract 要求的交付关注点的
Brief——这是 Phase 2 归因 `BRIEF` 的依据之一。

Reviewer 不应修改 Phase 1 的原始阅读结论来迎合 Brief。Phase 1 回答“实际读
到了什么”，Phase 2 回答“这和设计目标、Contract 要求有什么差异”。

Reviewer 产出单一的顶层 `repair_target`，定位最早错误层：

- `MANUSCRIPT`：正文未实现 Brief 已声明的意图——路由到 Authoring。
- `BRIEF`：Brief 本身不足以覆盖 Contract 要求的交付关注点，或与 Blind Read
  实际形成的理解存在结构性差异——路由到 Report Constructor。

Reader 的 `repair_target` 只有 `MANUSCRIPT | BRIEF`，不归因 `RESEARCH`。如果
正文需要的认知条件在 Brief 中不存在，归因 `BRIEF`；由能看到 accepted research
semantics 的 Constructor 判断是重建 Brief 还是升级 Research。同一轮同时出现
Brief 与 Manuscript blocker 时，most-upstream fault wins：归因 `BRIEF`。
Phase 2 不重新发现新问题、不规定具体修复方案。

## ReaderIssue 格式

Blocking Issue 必须满足：如果不修复，它会实质性破坏目标读者对主要报告承诺
的理解，或者会实质性阻止交付物达到要求的专业成品质量。每个 ReaderIssue 包含：

```text
observation      具体观察到的理解失败（什么位置发生了什么）
reader_effect    这对读者造成什么影响（读者必须自行做什么、失去了什么）
location         可选，具体定位（heading / 段落 / 跨节区间）
```

`repair_target` 是 Phase 2 的顶层归因，不是每个 issue 的字段。Reviewer 不应
把同一个上游认知缺陷拆成大量重复 issue——如果多个症状具有相同认知根因、指向
同一个最早错误层、可以通过同一次结构性修复共同消除，则应优先合并为一个
ReaderIssue。抓根因，不抓噪声。

## PASS 条件

PASS 不表示“完美”。PASS 表示：在目标读者假设下，没有剩余问题会实质破坏主要
认知交付，或实质阻止报告达到要求的专业成品质量。停止条件是
`repair_target is None`——不是 `quality_score >= threshold`，不是
`review_round >= N`，不是 Reviewer“总体满意”。PASS 的 `rationale` 可以为空。

Reader Gate PASS 认证的是一个具体组合：Report Brief version + Manuscript
version。Manuscript 修改或 Brief 修改都会使旧 PASS 失效。任何 Manuscript 修改
后必须 NEW Reviewer → Phase 1 → Phase 2，不能让同一 Reviewer 直接确认“我看到
你按我说的改了，所以 PASS”。Reader PASS 属于具体版本产物，不属于流程阶段。

## 最终原则

> **一个不知道作者内部意图的真实专业读者，是否能够仅凭最终 Reader Surface，
> 以合理认知成本形成 Report Brief 预期的领域理解，并把它当作成熟的专业成品。**
