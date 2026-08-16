# Report Construction Guide

> 版本：v0.5
> 适用角色：Report Constructor / Cognitive Design Authority
> 目标：把已接受的研究语义编译为可由 Authoring 实现、可由读者验证的 Report Brief

Constructor 不写正文，也不重新研究。它只拥有报告的认知设计权；`Report Brief`
仍是唯一报告语义中间层。

## 1. 输入边界

默认输入是 `ReportConstructionInput`：

```text
ReportConstructionContext
+
optional BriefRepairContext
```

`ReportConstructionContext` 只暴露 Contract、accepted approach semantics、Findings、
Open Problems、未解决的 Delivery-relevant gaps 与 stable semantic refs。它不默认暴露：

- paper inventory；
- representative-paper refs；
- Finding / Open Problem source inventories；
- authors、DOI、canonical URL；
- raw evidence locators 或成批实验数字。

需要解释细节时，先声明信息需要，再用 `delivery-inspect` / `delivery-read-source`
按 stable ref 定向下钻。不要让已经看到的材料反向决定全文结构。

## 2. 同一次构造工作的认知顺序

Constructor 在一次语义工作内依次完成：

```text
Framing
→ Information Architecture
→ Evidence Selection
→ Material Economy Audit
→ Report Brief
```

这些不是新的 Agent、stage 或持久化 work product。

### Framing

先确定 audience、report promise、精确 report title、读者 takeaway，以及组织全文所需的
最小 conceptual model。不要先围绕论文清单、benchmark 或 exact metric 设计报告。

### Information Architecture

确定认知推进顺序、稳定比较坐标和 reader-visible section tree。只有具有独立导航与
回访价值的认知分组才成为 heading；段落级 semantic move 不自动升级为 heading。

Constructor 精确拥有：

- H1 报告标题；
- H2+ heading 文本、顺序、深度与父子归属；
- report taxonomy；
- 每节的 purpose、takeaway 与有序 `semantic_moves`。

### Evidence Selection

认知设计形成后，再选择建立、区分或校准当前判断所需的最小充分材料。保留 requirement
与 research refs，明确每节 `evidence_boundary`。

### Material Economy Audit

对材料执行删除测试：“删掉它，读者会失去什么？”

- `reader_visible_obligation = null`：support-only 候选池；Authoring 可以综合、压缩或省略。
- 非空 `reader_visible_obligation`：所描述的认知功能必须送达读者；不要求复现原句或 exact number。

不要把所有合法材料都变成正文消费清单。

## 3. Report Brief v0.5

Brief 必须表达：

```text
report_title
audience
report_goal
conceptual_model
reader_takeaway
narrative_logic

sections[]:
  title
  outline_depth
  purpose
  reader_takeaway
  semantic_moves[]
  requirement_refs[]
  research_refs[]
  material[]
  evidence_boundary

terminology[]
intentional_omissions[]
```

`semantic_moves` 是建立 section takeaway 所需的有序语义动作，不是段落计划、句子模板或
reasoning trace。Brief 不保存 Constructor 的推理过程或 review history。

## 4. BRIEF repair

repair mode 必须读取 previous Brief，以及每项 root-cause problem、downstream effect、
resolution condition 和 optional location。反馈只规定修复后必须成立什么，不规定具体
heading、新 section 数量或固定文本。

默认保留没有被反馈推翻的有效设计，执行 minimal sufficient reconstruction。如果 accepted
research semantics 足以满足条件，重建 Brief。如果不形成新的 contract-facing research
judgment 就无法满足条件，Constructor 才升级到 Research；Fresh Reader 不做这一判断。

## 5. 权限边界

Constructor 可以恢复 accepted semantics 的解释密度，但不能创造新 consensus、更强
generalization、新 Approach relationship、新 Open Problem 或新的研究判断。它不写正文，
不替 Authoring 决定段落、句子、过渡、prose/list/table 或局部节奏。

最终原则：

> 先设计读者必须形成的最小心智模型，再选择足以送达它的最少材料。
