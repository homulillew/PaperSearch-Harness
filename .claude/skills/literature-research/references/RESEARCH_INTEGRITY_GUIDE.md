# Research Integrity Guide

> 版本：v0.5
> 适用角色：Research Integrity Reviewer / Research Claim Integrity Authority

本指南定义 AI / ML 技术调研报告的 Research Integrity 检查标准。

它不负责文风，不负责重新组织文章，也不决定 Research Run 是否完成。

它只回答一个问题：

> **当前报告中的每个重要判断，其强度是否与 Research State 和 Primary Evidence 的强度匹配？**

Research Integrity Reviewer 可以要求修改 Delivery，也可以在发现实质研究缺口时要求返回 Research。

不得通过“润色”补齐不存在的证据。

---

## 1. 基本原则

报告不得：

* 新增 Research State 中不存在的实质结论；
* 把相关性改写成因果关系；
* 把单篇论文结果扩大为领域共识；
* 把作者自述改写成独立验证事实；
* 把有限 benchmark 结果推广成一般能力；
* 删除会改变技术含义的实验条件；
* 隐去重要负面结果；
* 用更强语气替换不确定结论。

如果证据只能支持弱判断，应降低表述强度。

如果报告需要的新判断本身有价值但当前 State 没有证据，应：

> 返回 Research，而不是让 Authoring 自行补充。

---

## 2. 区分证据层级

至少区分：

### 作者报告

> 作者报告该方法在指定实验中优于其比较基线。

### 独立复现

> 后续独立工作在相似条件下复现了该趋势。

### 多篇一致证据

> 多篇相互独立研究在可比较条件下观察到类似结果。

只有第三类才更接近领域层面的稳定结论。

如果实际上只有一篇论文，应写：

> 该论文的实验显示……

而不是：

> 研究表明……

### 摘要与 discovery metadata 不是 Primary Evidence

报告中的机制级判断、实证结果与详细比较，必须来自 `inspect-source` / `read-source`
获得的一手原文，而不是 abstract 或搜索元数据。如果 State 中的 PaperAnalysis 仅基于
摘要，Integrity Reviewer 应将其视为证据不足，要求返回 Research 补充一手证据，而非
通过润色强化表述。详见 RESEARCH_PROTOCOL.md 的 Primary Evidence Gate。

---

## 3. SOTA 是高风险表述

“SOTA”必须有明确范围。

至少检查：

* benchmark；
* split；
* metric；
* model / method category；
* comparison set；
* 时间范围。

避免：

> 该方法达到 SOTA。

优先：

> 在作者报告的 X benchmark、Y split 和 Z metric 上，该方法高于论文列出的同期基线。

如果不同工作使用不同：

* backbone；
* input configuration；
* resource budget；
* context length；
* retrieval corpus；
* 重复评测次数；

则不能仅根据最高分判断方法机制更优。

---

## 4. 数值提升不等于统计显著

如果论文没有统计显著性检验，不使用：

> 显著提升

来表示 numerically higher。

优先：

> 指标由 71.4 提高至 73.7。

或：

> 提高 2.3 个百分点。

只有明确报告 statistical significance 时，才使用：

> 统计显著

---

## 5. 经验结果通常不构成“证明”

对于经验论文：

避免：

> 实验证明组件 P 提升任务能力。

优先：

> 在作者测试的系统和任务上，引入组件 P 后指标 M 提高。

“证明”主要适用于：

* formal theorem；
* mathematical proof；
* 明确的形式化正确性结论。

---

## 6. 泛化必须说明泛化到哪里

不要因为多个 benchmark 都提高，就写：

> 具有良好泛化能力。

应区分：

* in-domain；
* cross-dataset；
* cross-task；
* cross-model；
* OOD；
* temporal；
* environment transfer。

报告必须明确实际验证的是哪一种。

---

## 7. 鲁棒性必须说明扰动维度

“鲁棒”没有独立含义。

需要明确对什么变化鲁棒，例如：

* prompt variation；
* retrieval noise；
* adversarial perturbation；
* model variation；
* external dependency failure；
* long-context noise；
* incomplete observation；
* distribution shift。

优先：

> 在加入一定比例干扰输入后，该方法性能下降幅度小于 baseline。

而不是：

> 方法具有较强鲁棒性。

---

## 8. 效率必须说明是哪一种成本

“更高效”可能指：

* latency；
* throughput；
* FLOPs；
* operation count；
* external calls；
* memory；
* GPU hours；
* dollar cost。

报告只能对论文实际测量的维度作判断。

例如：

> 该方法减少平均操作次数，但论文未报告端到端 latency，因此不能判断实际运行时间是否更短。

不要用一个“效率”概括多个未测量维度。

---

## 9. 计算与外部资源预算必须显式考虑

资源敏感型方法尤其需要检查：

* input volume；
* output volume；
* 重复运行次数；
* external data calls；
* service calls；
* wall-clock latency；
* parallelism。

如果性能提升伴随明显更高 inference budget，报告必须说明。

不能自动把收益归因于方法机制。

---

## 10. 可扩展性必须说明尺度变量

不要因为“更大模型上也有效”就写：

> scalable

可扩展性可能针对：

* model size；
* dataset size；
* context length；
* component count；
* external service count；
* workload horizon；
* corpus size；
* concurrency；
* training cost。

必须写清论文实际验证了哪个尺度。

---

## 11. Benchmark 表现不等于一般能力

Benchmark 是特定任务分布和评测协议。

例如：

> 在 benchmark X 上提高 10%

只能直接说明：

> 方法在 benchmark X 所定义的任务和评测协议下得到更高分数。

不能直接推出：

> 一般能力 Q 提高 10%。

检查 benchmark 是否真实覆盖报告声称的能力。

---

## 12. 不同 benchmark 分数不能横向排序

即使两个 benchmark 都是百分制，也不能写：

> A 在 X 得 70，B 在 Y 得 75，所以 B 更强。

不同任务、metric 和评测协议之间没有天然可比性。

---

## 13. LLM-as-a-Judge 需要单独限定

如果结果依赖 LLM judge，检查是否报告：

* judge model；
* rubric；
* pointwise / pairwise；
* 是否 blind；
* 是否有人类一致性验证；
* judge 是否可能偏向某类输出。

不要把 judge score 写成无条件客观指标。

---

## 14. 方法比较必须考虑公平性

跨论文比较时至少检查：

* backbone；
* model size；
* training data；
* retrieval corpus；
* context window；
* sample count；
* inference budget；
* external resource access；
* data access interface；
* maximum steps；
* prompt；
* proprietary model；
* baseline 来源；
* extra supervision；
* extra adaptation procedure；
* hardware。

如果关键条件不同，应明确：

> 这些结果只能说明各方法在各自实验设置下的表现，不能构成严格的横向排序。

---

## 15. Ablation 不自动证明机制

例如：

> 去掉组件 P 后性能下降。

可以支持：

> 组件 P 对完整系统性能有贡献。

不能自动支持：

> 组件 P 通过过程 Z 提升性能。

应区分：

* component necessity；
* component contribution；
* causal mechanism；
* explanatory hypothesis。

作者对机制的解释如果没有独立证据，应保持为解释或假设。

---

## 16. 冲突必须确认双方真的可比较

不要因为 A 与 B 的结果不同就写：

> 领域存在争议。

先检查：

* base model；
* task；
* budget；
* metric；
* prompt；
* training regime；
* data contamination；
* evaluation protocol。

如果这些不同，更准确的是：

> 现有结果并不一致，但尚不能判断差异来自方法还是实验条件。

---

## 17. Research Gap 必须由已有证据推出

高质量 gap 应满足：

1. 已说明已有方法；
2. 已说明这些方法解决了什么；
3. 已说明它们共同没有解决什么；
4. gap 与前文 evidence 有直接关系。

避免：

> 未来可以探索更智能、更动态、更高效的方法。

如果没有当前 corpus 支撑，不应把想象中的新方向写成“领域空白”。

---

## 18. 区分不同的不确定性

报告应区分：

### 已有冲突

存在方向相反且可比较的证据。

### 单边证据

目前只有一侧结果，缺少独立复现或反例。

### 不可比较

论文很多，但 benchmark、模型或 budget 差异导致无法排序。

### 证据不足

论文进行了相关实验，但不足以支持它自己或报告中的强结论。

### Corpus-bounded absence

本次检索范围中没有找到直接证据。

最后一种不能写成：

> 没有任何工作研究……

应限定：

> 截至本次检索截止日，在当前纳入和检索到的公开文献中，尚未发现……

---

## 19. 时间敏感和绝对表述必须特别审查

高风险词包括：

* 首次；
* 唯一；
* 全部；
* 所有；
* 没有任何；
* 从未；
* 当前 SOTA；
* 主流；
* 普遍；
* 一致认为；
* 已形成共识；
* 完全解决；
* 根本原因；
* 必然。

这些表述必须有足够 evidence 和 scope。

优先增加边界：

> 在作者比较的 baseline 范围内……

> 在本次纳入的文献中……

> 在该 benchmark 和模型设置下……

> 截至本次检索截止日……

不要把 Corpus Search History 本身当 proof；它只能帮助限定 absence claim 的范围。

---

## 20. 数字必须对应实际证据

报告中的关键数字应能追溯到：

* retained Paper；
  -具体 locator；
  -具体 experiment / table / figure；
  -对应模型和任务设置。

不能：

* 从 abstract 推导更详细数字；
* 合并不同实验 setting；
* 对不同论文结果直接平均；
* 把相对提升和百分点混用；
* 忽略 baseline。

---

## 21. Citation 与 claim 应一一匹配

Integrity Reviewer 应检查：

> 这个 citation 实际支持前面的哪一句？

一个 citation 不应被用于覆盖一段中的多个不同事实。

特别检查：

* motivation ≠ evidence；
* introduction claim ≠ verified result；
* author interpretation ≠ causal proof；
* provider summary ≠ Primary Evidence；
* search snippet ≠ Research State。

---

## 22. Report 推断必须与 State 区分

报告可以进行 synthesis，但不能创造新的 substantive research knowledge。

允许：

> 根据 State 中已有多篇论文关系，重新组织并表达一个已经存在的综合判断。

不允许：

> Authoring 在阅读 Delivery View 后自行产生 State 中从未形成的新领域结论。

如果新的 synthesis 实际构成重要研究判断：

> 返回 Research，形成正式 Finding，再继续 Delivery。

---

## 23. Integrity Reviewer 的处理方式

Integrity 与 Fresh Reader 独立，只检查 research fidelity，不检查 prose quality。它只裁决，
不直接编辑 Brief、Manuscript 或 Research State。发现问题后按最早有资格修复的层归因。

### MANUSCRIPT

例如：

* 语气过强；
* citation 放置错误；
* 数字表述错误；
* scope 限定遗漏；
* 把 author claim 写成共识。

可以在 Delivery 中修正。

### BRIEF

Research State 正确，但 Brief 的 evidence boundary、generalization、材料选择或 conceptual
framing 已经不忠实于 accepted semantics。返回 `REVISE_DELIVERY(target=BRIEF)`。

### RESEARCH

例如：

* 缺少支撑关键结论的 Primary Paper；
* 路线之间需要新的实质比较；
* 某项重要冲突尚未验证；
* 最新发展没有进入 State；
* 新的 Open Problem 需要额外证据。

必须：

> REOPEN_RESEARCH

Integrity Reviewer 不自行 broad search，不自行补 PaperAnalysis，不直接修改 Landscape。

---

## 24. 最终检查

交付前重点确认：

* 单篇论文没有被写成领域共识；
* author claim 与独立证据有明确区别；
* correlation 没有升级为 causation；
* ablation 没有升级为机制证明；
* benchmark 结果没有升级为一般能力；
* SOTA、显著、泛化、鲁棒、效率、scalable 都有明确范围；
* 方法比较考虑了 backbone、budget 和评测差异；
* 额外计算和外部调用预算被如实说明；
* absolute / time-sensitive claim 有 scope；
* 重要数字和 citation 可追溯；
* corpus-bounded absence 没有被写成 universal absence；
* 报告没有创造 Research State 之外的实质结论。

最终原则：

> **判断可以明确，但判断强度必须与证据强度一致。**
