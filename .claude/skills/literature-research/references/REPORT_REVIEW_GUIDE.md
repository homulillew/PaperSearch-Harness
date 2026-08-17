# Report Review Guide

> 版本：v0.6
> 适用角色：Fresh Reader / Reader Acceptance Gate
> 目标：验证最终稿是否真正作为面向目标读者的认知载体成立
> 不负责：研究完成性判断、Research State 修改、Primary Evidence 最终核验、事实修复

---

## 1. 目的与边界

Fresh Reader 回答的是：

> **这篇报告作为文章是否成立？目标读者能否仅凭正文形成连续、稳定、可复述的
> 领域理解？**

它不回答：

> **这些研究判断最终是否真的被 Research State 与 Primary Evidence 支持？**

后者属于 Research Integrity Review。因此 Reader Gate 与 Research Integrity Gate
必须保持独立。

Reader Gate 主要检查：

- 读者实际形成了什么理解；
- 认知路径是否连续；
- 主要判断是否被文章真正建立；
- 材料是否服务于论证；
- 哪里需要回读、由读者自行补桥、无法判断材料用途或发生章节认知重启；
- 文章实际呈现的认知结构是否与 Brief 的编辑意图一致；
- 问题究竟属于 Manuscript，还是属于 Brief / Report Construction。

Reader Gate 不负责：判断 Research State 是否完整；判断某项 Finding 是否应被
修改；根据论文重新形成新的领域判断；调整 Approach Family；将用户或 Reviewer
的研究意见直接写入 Research State；通过自行重新研究来修正文稿。

---

## 2. 审查原则

Fresh Reader 不是“编辑老师”，也不是“风格打分器”。它的核心任务不是问
“我觉得这篇文章写得好吗？”，而是问：

> **作为第一次读者，我实际建立了什么理解？哪里无法继续建立理解？为什么？**

Reviewer 应优先报告：具体位置 + 具体理解失败 + 该失败影响哪个主要判断或
认知路径，而不是“结构比较清晰、语言较为流畅、专业性 8/10”之类的打分。
本 Guide 不使用统一质量分数。

---

## 3. 两阶段冷读

每个新 Manuscript 必须由新的 Reviewer instance 进行审查。任何 Manuscript
修改都会使旧 Reader PASS 失效。新的 Reviewer 必须重新从 Phase 1 开始。

```text
Phase 1 — Blind Read
不看 Report Brief
→ 重建读者实际获得的认知结构

Phase 2 — Brief Check
读取 Report Brief
→ 比较“设计的认知路径”和“实际读出的认知路径”
```

两阶段不能倒置。如果 Reviewer 一开始就读取 Brief，它会知道作者原本想表达
什么，并可能自动替文章补全正文缺失的逻辑。这会削弱 first-time-reader
validation。

---

# Phase 1 — Blind Read

## 4. 盲读输入

Phase 1 只接收：

```text
Deliverable description
Audience
Report Review Guide
Manuscript (rendered Reader Surface)
```

Phase 1 不接收 Report Writing Guide，也不接收 Report Brief、Research State、
Paper Analysis、Approach Family、Finding、Open Problem、Primary Source、
Constructor notes、Authoring notes 或前一轮 Reviewer 的推理记录。

`Audience` 可以来自当前 Report Brief 的窄投影，但只能暴露目标读者描述本身；
不得因此暴露 Brief 的 `promise`、`frame`、`arc`、`focus`。必要时可以知道
目标受众和交付要求，因为真实读者通常知道自己为什么在读这份报告。但
Reviewer 不应获得作者隐藏的认知设计。

## 5. 第一次阅读纪律

Reviewer 应尽可能模拟一个真实专业读者，而不是逐句寻找问题。第一次阅读时：
正常从头到尾阅读；不提前构造修订方案；不因为自己“猜到了作者想表达什么”
而自动补全缺失关系；标记第一次明显需要回读的位置；标记第一次不知道“这段
为什么在这里”的位置；标记第一次出现无法解释其作用的数字、术语或论文；
标记章节切换时是否需要重新建立新的局部模型。

Blind Read 的重点不是尽量发现更多问题，而是观察真实阅读状态如何变化。

### 5.1 不得用 Reviewer 自身知识替正文补桥

Reviewer 能够推断出某个缺失关系，不代表文章已经把该关系传递给读者。当重要
关系主要依赖 Reviewer 自身领域知识、对作者意图的猜测、后文才出现的信息、
Brief 中的设计说明或对 Research State 的了解才能被补全时，Reviewer 应明确
区分“文本已经建立的关系”与“Reviewer 自行补出的关系”。不得因为 Reviewer
最终“猜对了”，就免除认知跳步。

核心原则：

> **Reviewer 能推出来，不等于读者被文章带到了那里。**

## 6. 核心认识重建

完成第一次阅读后，不回看 Report Brief，Reviewer 应回答：

- 全文核心认识：用不超过 3 句话回答“这篇报告最终告诉了我什么？”如果只能说
  “它介绍了 A、B、C、D 几种方法”，通常说明报告只有覆盖，没有形成更高层认识。
- 领域心智模型：读完后我现在如何理解这个领域？应尽量重建核心问题、主要约束、
  主要技术路线为什么出现、各路线真正改变什么、路线之间最重要的差异、当前
  证据支持哪些总体判断、仍然有哪些未知。
- 稳定比较坐标：报告实际使用哪些稳定维度比较不同路线？如果每个章节使用完全
  不同的比较方式，Reviewer 应记录为潜在认知结构失败。

## 7. 认知连续性检查

- **认知跳步**：是否存在从 A 直接到 C，但理解 C 所需的 B 尚未建立？常见表现：
  结论突然加强、比较突然切换维度、从实验结果直接跳到部署结论、从局部论文
  结果直接跳到领域判断、从机制描述直接跳到优劣排序。
- **认知重启**：新章节是否继承前文已经建立的模型？典型表现：每节重新介绍
  背景、重新定义问题、重新建立比较标准。Reviewer 应判断前一节的理解是否
  降低了后一节的理解成本。
- **认知债务**：报告是否让读者长期记住大量尚未解释其意义的信息？重点关注
  连续方法名、密集数字、benchmark 名称、缩写、尚未解释的分类、尚未消费的
  对比结果。问题不在信息数量本身，而在这些信息是否很快获得认知归宿。
- **论证孤岛**：是否存在正确但与前后主论证没有连接的材料？可用删除测试：
  如果删除这一段，全文主要认知链是否几乎不变？

## 8. 论证完整性检查

对于全文的重要判断，Reviewer 应检查读者能否找到：判断、机制/原因、证据、
条件/限制、含义。不要求机械五件套，重点是读者是否知道为什么应该接受这个
判断。检查判断是否清楚、机制是否足够、证据是否被解释、限制是否进入主要
判断、含义是否被建立。

## 9. 材料经济性检查

Reviewer 应主动寻找正确但没有必要认知功能的材料。对重要材料问：它是在建立
认识、区分路线、还是校准判断？如果一个判断已经被充分建立，检查后续同类
论文和数字有没有增加新认知，是否只是重复证明相同结论。每个重要数字出现后，
Reviewer 应能回答“这个数字在当前论证中完成了什么任务？”如果删除论文名后
一段文字失去全部组织结构，则该段可能仍然是 paper-by-paper summary。

## 10. 信息呈现检查

Reviewer 不要求固定使用 prose、list、table 或 figure，只检查当前呈现是否
降低理解成本。连续推理是否被机械拆碎成大量 bullet？平行信息是否难以扫描？
表格是否存在共同坐标、是否只是论文列表换形式、正文是否逐格复述？标题是否
承担认知导航——读者扫标题时应大致知道文章在推进什么问题。

## 11. 专业中文检查

Fresh Reader 只处理以下两类达到阻塞阈值的语言问题：实质增加目标读者的认知
成本或破坏主要认知交付；实质使交付物无法达到其要求的专业成品质量。不要把
Review Loop 变成无限润色。重点检查句法是否持续遮蔽技术关系、主语和动作是否
长期不明确、抽象名词是否替代具体机制、同一个判断是否反复换词重述、模板化
连接词是否大量替代真实逻辑、段落是否异常均质、AI 生成节奏是否显著干扰连续
阅读。单个“值得注意的是”不构成 blocker。只有当这些模式反复、系统性出现并
满足上述任一阈值时才应报告。孤立措辞偏好、单句可优化或纯审美差异不构成
blocker。

## 12. 全文闭合检查

完成 Blind Read 后，Reviewer 应检查：结论是否来自正文（是否引入新分类、
新判断、新因果、新比较坐标）；分类体系是否漂移（正文按 X 分类、结论按 Y
分类不一定错误，但必须解释二者关系）；开放问题是否自然产生（是否来自前文
已经建立的限制和证据缺口，还是额外附上的“未来方向”）；文章为何在这里结束
（主要报告承诺是否已经闭合，而不是“材料似乎写完了”）。

## 13. Phase 1 只诊断，不归因

Blind Read 阶段只回答：哪里发生了真实理解失败，或足以阻止专业成品成立的
系统性缺陷？不要在这一阶段决定 `MANUSCRIPT` / `BRIEF` / `RESEARCH` 等修复
目标。原因是提前猜测故障层会反向影响 Reviewer 对正文的阅读。

正确顺序：

```text
Phase 1 发现并描述理解失败
        ↓
冻结 Blind Read 结果
        ↓
Phase 2 读取 Report Brief
        ↓
比较设计认知与实际认知
        ↓
再进行故障归因
```

即：**先诊断，再归因。**

---

# Phase 2 — Brief Check

## 14. Brief Check 输入

Phase 2 在完成 Blind Read 记录后才接收 `Report Brief`。Reviewer 不应修改
Phase 1 的原始阅读结论来迎合 Brief。Phase 1 回答“实际读到了什么？”，
Phase 2 回答“这和设计目标有什么差异？”。

## 15. 设计认知与实际认知对比

Reviewer 应比较 Brief 的 `promise` / `arc` 与 Blind Read 实际复述结果，比较
Brief 的 `frame` 与 Blind Read 实际使用的比较坐标。重点找：

- **设计认识没有进入正文**：Brief 承诺建立某种认识，但 Blind Reader 只得到
  覆盖性罗列。这属于严重认知传递失败。
- **正文弱化了 Brief**：Brief 中存在重要限定，但正文为了顺滑而删除。
- **正文加入了 Brief 未授权的重要判断**：可能是 Authoring 越界。最终是否
  研究上错误，由 Integrity 判断；Fresh Reader 只指出正文出现了 Brief 之外
  的重要认知。
- **Brief 本身设计不足**：如果 Manuscript 忠实执行 Brief，Blind Read 仍然
  无法形成连续理解，说明问题可能不在 Authoring（例如 Brief 的 `arc` 本身
  存在跳步、`frame` 缺少稳定比较坐标）。此时应路由到 Report Construction。

## 16. 故障归因

Reviewer 必须尽量定位**最早错误层**，并产出单一的顶层 `repair_target`。

### 16.1 Manuscript Fault → Authoring

典型：一个关键过渡没有写出来；某段同时承担多个认知任务；一个限制条件被
正文弱化；表格与正文重复；某段语言明显难读；Brief 已明确比较坐标但 Authoring
没有贯彻。路由：`repair_target = MANUSCRIPT`。

### 16.2 Brief Fault → Report Constructor

典型：全文没有稳定比较框架；章节设计导致持续认知重启；关键 synthesis 没有
被设计；Brief 的 `arc` 本身存在认知跳步；`focus` 错误导致关键认知环节缺失。
路由：`repair_target = BRIEF`。

### 16.3 Reader 不归因 RESEARCH

Fresh Reader 的 `repair_target` 只有 `MANUSCRIPT | BRIEF`。如果正文需要的
认知条件在 Brief 中不存在，归因 `BRIEF`；随后由能看到 accepted research
semantics 的 Constructor 判断是重建 Brief，还是升级 Research。Reader 不返回
疑似或确认的 Research fault。

v0.6 移除了自动收敛循环：Reader 只做一次决定。如果 `repair_target` 非
`None`，Runtime 不再自动 revise 后重读；由宿主（Claude）重新 authoring 后
重新运行 Reader。同一轮同时出现 Brief 与 Manuscript blocker 时，
most-upstream fault wins：归因 `BRIEF`。Brief 重建后，旧 Manuscript blocker
不作为新稿的强制修复清单；新稿接受全新冷读。

---

## 17. ReaderIssue 定义与格式

Blocking Issue 必须满足以下至少一项：

> 如果不修复，它会实质性破坏目标读者对主要报告承诺的理解；或者会实质性阻止
> 交付物达到要求的专业成品质量。

每个 ReaderIssue 至少包含：

```text
observation      具体观察到的理解失败（什么位置发生了什么）
reader_effect    这对读者造成什么影响（读者必须自行做什么、失去了什么）
why_blocking     为什么这实质破坏主要认知交付或专业成品质量
location         可选，具体定位（heading / 段落 / 跨节区间）
```

v0.6 不再要求每个 issue 携带 `repair_target` 或 `resolution_condition`：
`repair_target` 是 Phase 2 的顶层归因，不是每个 issue 的字段；`resolution_condition`
属于 Brief repair feedback，不属于 Reader issue。Reviewer 不应把同一个上游
认知缺陷拆成大量重复 issue——如果多个症状具有相同认知根因、指向同一个最早
错误层、可以通过同一次结构性修复共同消除，则应优先合并为一个 ReaderIssue。
目标是：**抓根因，不抓噪声。**

通常不应成为 blocker：Reviewer 个人更喜欢另一种措辞；单个句子可以更漂亮；
单个连接词稍显模板；不影响认知链的轻微篇幅问题；纯审美性的标题偏好。

---

## 18. PASS 条件

Reader Gate PASS 不表示“这篇文章完美”。PASS 表示：

> 在目标读者假设下，没有剩余问题会实质破坏主要认知交付，或实质阻止报告
> 达到要求的专业成品质量。

具体意味着：全文核心认识可以被复述；主要认知路径连续；关键比较坐标可重建；
没有严重认知跳步；没有系统性认知重启；主要论证能够被追踪；重要材料具有
明确功能；结论与正文闭合；Blind Read 实际获得的认知结构与 Brief 基本一致；
不存在系统性破坏专业成品要求的语言或呈现根因；剩余问题属于非阻塞性的局部
优化。

停止条件：

```text
repair_target is None  且  blocking_issues == ()
```

不是：

```text
quality_score >= threshold
review_round >= N
Reviewer “总体满意”
```

---

## 19. Reader PASS 绑定具体版本

Reader Gate PASS 认证的是一个具体组合：

```text
Report Brief version
+
Manuscript version
```

因此 Manuscript 修改 → 旧 Reader PASS 失效；Report Brief 修改 → 旧 Reader
PASS 失效。Runtime 不判断修改是否“足够小”。原则：

> **Reader PASS 属于具体版本产物，不属于流程阶段。**

Phase 1 的 Blind Read 记录也必须绑定它实际阅读的 Manuscript 版本，并在
Phase 2 暴露 Brief 前冻结。

## 20. 新鲜审查不变量

任何 Manuscript 修改后：`old Reader PASS → invalid`。新的 Manuscript 必须：
`NEW Reviewer → Phase 1 Blind Read → Phase 2 Brief Check`。不能让同一
Reviewer 直接确认“我看到你按我说的改了，所以 PASS”，因为它已经知道作者意图
和前一轮缺陷，无法再模拟真正第一次阅读。

## 21. 人类校准

Reader Gate 是机器质量保障，不是人类接受的替代。如果出现
`AI Reader → PASS` 但 `Human Reader → 明确理解失败`，应把人类反馈视为重要
校准信号。后续应判断：Review Guide 是否没有正确执行已有标准；目标读者假设
是否错误；Blind Reader 是否过度依赖模型自身推断能力；Report Brief 是否错误
估计真实读者前置知识。AI PASS 不能覆盖真实人类的明确负面阅读反馈。

---

## 最小审查清单

Phase 1 必须至少回答：

```text
1. 全文核心认识是什么？
2. 我实际形成了什么领域模型？
3. 主要比较坐标是什么？
4. 哪里存在认知跳步？
5. 哪里存在认知重启？
6. 哪里存在认知债务或论证孤岛？
7. 重要材料是否都有明确作用？
8. 主要判断是否有可追踪论证？
9. 结论是否与正文闭合？
```

Phase 2 必须至少回答：

```text
10. Blind Read 得到的认识是否与 Brief 的 promise / arc 一致？
11. 实际比较坐标是否与 Brief 的 frame 一致？
12. 是否遗漏、弱化或新增重要认知？
13. 多个症状是否应合并为同一根因级 ReaderIssue？
14. 最早错误层属于 Manuscript 还是 Brief（顶层 repair_target）？
15. 是否仍存在 blocking issue？
```

---

## 最终原则

Fresh Reader 的职责不是替作者把文章改得更漂亮。它的职责是验证：

> **一个不知道作者内部意图的真实专业读者，是否能够仅凭最终 Reader Surface，
> 以合理认知成本形成 Report Brief 预期的领域理解，并把它当作成熟的专业成品。**

因此 Reader Gate 的核心不是“文章是否符合某个 Reviewer 的完美文风偏好？”，
而是：

> **文章是否真的交付了正确的认知结构，并作为要求的专业成品成立？**
