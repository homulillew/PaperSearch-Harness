# literature-research

`literature-research` 是一个面向 Claude Code 的学术论文调研 Skill。

它用于处理开放式文献研究任务：从研究问题出发，持续搜索、筛选和阅读论文，整理主要技术路线与研究结论，最终生成带可核查引用的领域调研报告。完成后的研究还可以发布为本地 Wiki，供后续任务继续使用。

研究过程保存在结构化状态中，可以跨 Claude Code 会话恢复。论文、分析、技术路线、研究发现和待解决问题都会留下明确记录，便于继续研究、检查结论和追溯来源。

## 架构

```text
            Claude Code
      research decisions
               │
               ▼
        Python Harness
   actions · rules · persistence
               │
               ▼
         Research State
               │
        ┌──────┴──────┐
        ▼             ▼
     Report       Local Wiki
```

Claude Code 负责理解研究任务、选择下一步行动、阅读论文和进行领域综合。

Python Runtime 负责生命周期、状态修订、稳定引用、来源记录、持久化、引文渲染和交付物校验。

研究状态连接两者：每一次有效研究都会更新 State，后续搜索和阅读再根据新的 State 继续推进。

## 核心能力

### Research Contract

每次研究开始时都会建立 Research Contract，用于记录：

* 研究目标；
* 需要回答的问题；
* 研究范围和排除项；
* 最终交付内容。

Contract 提供研究边界和完成依据。搜索次数、论文数量和分析数量只用于描述工作量，不直接决定研究是否完成。

### 自适应研究循环

研究过程围绕当前状态持续推进：

```text
查看当前状态
→ 识别关键问题
→ 搜索或读取证据
→ 更新研究状态
→ 重新评估
→ 决定下一步
```

搜索、深读和综合会交替进行。

例如，新的论文可能暴露一条此前没有覆盖的技术路线；进一步阅读又可能发现不同论文的实验预算并不可比。后续研究会根据这些变化调整方向。

### 多来源论文发现

主要论文发现由 DeepXiv 提供。

对于最新论文、快速变化的研究方向，以及学术语义搜索可能遗漏的工作，Claude Code 还可以使用原生 Web Search 进行独立补充检索。

典型流程为：

```text
DeepXiv
→ 学术语义检索

Web Search
→ 最新论文与遗漏检查

Primary Source
→ 正式证据
```

搜索结果、网页摘要和元数据用于发现候选论文。详细技术结论需要回到论文原文确认。

### Primary Evidence

涉及以下内容的分析，应基于一手论文内容：

* 方法机制；
* 模型或算法设计；
* 实验结果；
* Ablation；
* 局限性；
* 跨论文比较；
* 定量结论。

论文被保留后，可以通过：

```text
inspect-source
read-source
```

读取相关章节、表格、实验设置或附录。

摘要和搜索结果适合用于筛选论文，不应单独支撑详细的机制性或实验性判断。

### 结构化研究状态

研究过程中形成的知识会保存为结构化对象，包括：

```text
Research Contract
Papers
PaperAnalysis
Approach Families
Findings
Open Problems
Investigation Gaps
Completion Checks
```

这些状态构成研究过程的长期上下文。

会话中断后，Claude Code 可以重新读取当前状态、开放问题和历史研究记录，再继续下一步工作。

### Completion Check

当研究者认为当前证据已经覆盖 Research Contract 时，会进入独立 Completion Check。

检查结果有三种：

```text
PASS
CONTINUE
UNCERTAIN
```

检查内容包括：

* Research Contract 是否得到覆盖；
* 主要技术路线是否完整；
* 代表性论文是否有足够的一手证据；
* 关键比较是否成立；
* 是否存在尚未解决的重要研究缺口。

`CONTINUE` 会将具体问题带回 Research 阶段继续处理。

### 报告生成

Completion Check 通过后，系统根据已接受的 Research State 生成调研报告。

报告阶段包括：

* 内容组织；
* 跨论文综合；
* 编辑检查；
* 研究诚信检查；
* 引文解析；
* 最终交付物校验。

报告中的关键判断应能够追溯到对应论文和来源。

### Local Wiki

正常完成并关闭的研究可以进一步发布为本地 Markdown Wiki。

Wiki 保存适合跨任务复用的内容，例如：

* 技术路线；
* 领域发现；
* 开放问题；
* 相关论文。

Wiki 可以帮助后续研究更快定位值得继续调查的方向。

正式研究结论仍以 Primary Paper 为证据来源。

## 安装

将 `literature-research` 目录放入 Claude Code 可以发现的 Skills 目录。

### 1. 创建 Python 环境

进入 Skill 目录：

```text
python scripts/setup.py
```

Windows 如果通过 `py` 启动 Python：

```text
py scripts/setup.py
```

安装脚本会创建 Skill 自己的 Python 环境并安装 Runtime 依赖。

### 2. 配置 DeepXiv Token

首次使用时运行：

```text
python scripts/harness.py configure-token
```

Token 通过交互输入，不会显示在终端中。

默认保存位置：

```text
~/.literature-research/deepxiv-token
```

也可以通过：

```text
DEEPXIV_TOKEN
```

临时覆盖本地保存的 Token。

不要把 Token 写入：

* `.env`；
* Skill 目录；
* research workspace；
* Git 仓库；
* Harness JSON 输入文件。

### 3. 检查环境

运行：

```text
python scripts/doctor.py --workspace PATH
```

Doctor 会检查：

* Python 环境；
* Runtime；
* DeepXiv 依赖；
* DeepXiv Token；
* Skill references；
* workspace 可写性。

检查通过后即可开始研究。

## 使用

研究数据应保存在 Skill 安装目录之外。

例如：

```text
my-project/
├── .claude/
│   └── skills/
│       └── literature-research/
└── workspace/
```

在项目目录中启动 Claude Code，然后调用：

```text
/literature-research 调研任务 T 的主要技术路线、证据边界和最新进展
```

也可以提出更完整的研究要求，例如：

```text
/literature-research

调研任务 T 的主要技术路线。

重点比较方法 A、方法 B 和方法 C 分别改变什么、
依赖哪些条件，以及各自的主要收益与代价。

希望说明不同方法的机制关系，比较实验条件和 baseline 是否公平，
并覆盖能够可靠验证的最新工作。

最终生成一份面向技术读者的中文领域调研报告。
```

Claude Code 会根据 Research Contract 和当前 Research State 自行决定搜索、阅读和综合顺序。

## Harness

所有确定性的研究状态操作统一通过：

```text
python "<SKILL_DIR>/scripts/harness.py"
```

执行。

研究命令通常需要指定 workspace：

```text
python "<SKILL_DIR>/scripts/harness.py" --workspace PATH ...
```

不要直接编辑 Runtime 生成的状态文件。

完整命令和 JSON 输入格式见：

```text
references/RUNTIME_API.md
```

## 研究流程

一次完整研究通常经历：

```text
Research Question
      │
      ▼
Research Contract
      │
      ▼
   RESEARCH
      │
      │ search / read / synthesize
      │          ↺
      ▼
COMPLETION_CHECK
      │
      ├── CONTINUE ──→ RESEARCH
      ├── UNCERTAIN ─→ RESEARCH
      │
      └── PASS
           │
           ▼
        DELIVERY
           │
           ▼
         CLOSED
```

研究过程不会依靠固定论文数量或搜索次数推进生命周期。

Completion 根据 Research Contract、当前研究状态和证据质量判断。

## Research State

一个 Research Run 会逐步积累：

```text
ResearchRun
├── ResearchContract
├── Lifecycle
├── Papers
│   └── PaperAnalysis
├── ApproachFamilies
├── Findings
├── OpenProblems
├── InvestigationGaps
├── CompletionChecks
└── DeliveryBasis
```

其中：

* `Paper` 保存论文身份和来源；
* `PaperAnalysis` 保存论文级分析；
* `ApproachFamily` 描述主要技术路线；
* `Finding` 保存跨论文研究发现；
* `OpenProblem` 描述领域尚未解决的问题；
* `InvestigationGap` 记录当前 Research Run 中仍需解决的研究任务。

Research State 是运行过程中的权威状态。

聊天记录、临时搜索结果和草稿不承担这一职责。

## 目录结构

```text
literature-research/
├── SKILL.md
├── references/
│   ├── RESEARCH_PROTOCOL.md
│   ├── RUNTIME_API.md
│   ├── COMPLETION_GUIDE.md
│   ├── REPORT_CONSTRUCTION_GUIDE.md
│   ├── REPORT_WRITING_GUIDE.md
│   ├── REPORT_REVIEW_GUIDE.md
│   └── RESEARCH_INTEGRITY_GUIDE.md
├── scripts/
│   ├── setup.py
│   ├── doctor.py
│   └── harness.py
└── runtime/
    ├── requirements.txt
    └── src/
        └── my_search_harness/
```

各文件职责如下：

| 文件                            | 说明                            |
| ----------------------------- | ----------------------------- |
| `SKILL.md`                    | Claude Code Skill 主指令         |
| `RESEARCH_PROTOCOL.md`        | 研究流程、证据获取和状态更新规则              |
| `RUNTIME_API.md`              | Harness 命令及输入格式               |
| `COMPLETION_GUIDE.md`         | Completion Check 判断规则         |
| `REPORT_CONSTRUCTION_GUIDE.md` | Constructor 的编辑设计与 Lean Report Brief 规范 |
| `REPORT_WRITING_GUIDE.md`     | Authoring 的正文实现规范     |
| `REPORT_REVIEW_GUIDE.md`      | Reader Gate 的两阶段盲读与归因规范   |
| `RESEARCH_INTEGRITY_GUIDE.md` | 证据强度、比较有效性和研究诚信要求             |
| `scripts/setup.py`            | 创建本地运行环境                      |
| `scripts/doctor.py`           | 检查安装和 workspace               |
| `scripts/harness.py`          | Claude Code 与 Runtime 的统一命令入口 |
| `runtime/`                    | 随 Skill 发布的 Python Runtime    |

## 设计原则

### 语义判断交给 Claude

论文筛选、技术路线划分、证据解释、研究缺口判断和报告综合都需要语义推理。

这些工作由 Claude Code 完成。

### 确定性行为交给 Runtime

状态修订、生命周期、稳定引用、持久化和引文渲染适合由程序严格执行。

这些行为由 Python Runtime 管理。

### State 驱动后续研究

每次有效研究都会更新 Research State。

下一步行动需要结合更新后的状态重新判断，使搜索、阅读和综合能够随着新证据调整。

### 证据强度决定论断强度

详细技术结论优先建立在 Primary Paper 上。

实验结果、机制描述和跨论文比较需要保留适当的来源与条件，避免将有限实验结果扩展成过强的领域结论。

### 完成条件由研究内容决定

论文数量、搜索次数和分析数量可以描述工作量，但不作为完成标准。

研究是否完成取决于：

* 研究问题的覆盖程度；
* 主要技术路线的代表性证据；
* 关键结论的可验证性；
* 重要冲突和局限是否得到处理；
* 剩余研究缺口是否影响最终交付。

## 适用场景

`literature-research` 适合需要系统整理论文证据的研究任务，例如：

* 技术路线调研；
* 新兴研究方向分析；
* 方法比较；
* Benchmark 和评测体系梳理；
* SOTA 与最新进展跟踪；
* 长周期论文研究；
* 为技术报告或研究决策建立文献基础。

对于只需要快速查找一两篇论文的任务，直接使用普通学术搜索通常更加简单。
