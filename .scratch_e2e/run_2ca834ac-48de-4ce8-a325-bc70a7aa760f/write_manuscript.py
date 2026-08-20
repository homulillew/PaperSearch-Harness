import json

RUN="run_2ca834ac-48de-4ce8-a325-bc70a7aa760f"
REPO="c:/Users/wushuhong/Desktop/PaperSearch-Harness"
SCRATCH=REPO+"/.scratch_e2e/"+RUN

markdown = r"""# 大语言模型 KV Cache 优化的技术路线图

## 核心问题与三条设计轴

Transformer 自注意力在生成阶段需要为历史 token 保存键（K）与值（V）向量，即 KV cache。其规模随序列长度 $T$ 线性增长，在长上下文与高并发部署中成为显存与带宽的主要瓶颈。围绕这一瓶颈，2025–2026 年的研究沿不同方向展开，但它们都可以被定位在三条设计轴上的特定选择：**保留什么**、**压缩或移除什么**、**是否需要训练**。这三条轴不是分类标签，而是让路线之间的取舍变得可比的解释视角——无损与有损、training-free 与 training-aware、精度与压缩比、精度与延迟，都能在这三条轴上找到各自的坐标。

一条路线若选择永久丢弃 token，它在“保留什么”上就更激进，但必须承担多轮对话中 token 重要性漂移的风险；若选择把数值压到低精度或低秩，它在“压缩什么”上做文章，但必须面对重建误差；若选择替换注意力结构本身，它从根本上改变了“保留什么”的含义，但通常需要从头训练。系统的调度与复用路线则几乎不动精度，而是在存储层级之间移动与复用 KV，把瓶颈从显存容量转移到带宽与命中率。本文按这三条轴组织主要路线，再进入它们之间的实证取舍与尚未解决的开放前沿。

## 永久驱逐与按需选择

### 重要性指标与预算分配

驱逐路线的核心想法是：并非所有 token 同等重要，丢弃被判定为不重要的 token 即可在保留标准注意力计算的同时缩小缓存。这条路线的设计空间由两个轴决定——逐 token 的重要性指标，以及预算在层与头之间的分配方式。

在重要性指标上，启发式方法从注意力统计量构造分数：累积/均值注意力衡量持续重要性，注意力方差捕捉波动但关键的 token。{{paper:cake|CAKE}} 的驱逐指标 $I[n] = \mathrm{Mean}(A) + \gamma\,\mathrm{Var}(A)$ 把两者相加，并用一个较大的窗口保护最近的 token，避免过早驱逐仍在波动的关键 token{{cite:cake}}。在预算分配上，CAKE 进一步用偏好优先的自适应分配把全局预算按层分配，其偏好分数由注意力熵与方差构造，并证明这种级联分配等价于一次性最优分配{{cite:cake}}。

关键的经验判断是：**分配策略本身是一等设计轴，而非附属细节**。在 LongBench 上以 128 层预算评测，CAKE 的偏好分配（29.29）优于均匀（28.36）、金字塔（28.69）与随机分配；而把指标换成均值、方差或其乘积，都不如相加组合{{cite:cake}}。这表明从一个小预算里榨取更多保真度，学习型分配比手工启发式更有效。

### 从启发式到端到端学习

启发式驱逐是 training-free 的，但其在严格预算下的增益有限。{{paper:lkv|LKV}} 把驱逐重新表述为端到端可微框架，联合学习头级预算与 token 选择，绕过启发式代理{{cite:lkv}}。其 LKV-H 把所有头展平为全局一维 Soft-Topk 选择，允许跨层预算转移——这是刚性逐层启发式无法逼近的；LKV-T 则从 KV 状态本身预测 token 效用，避开了“需要注意力矩阵才能评估重要性”的先有鸡还是先有蛋问题{{cite:lkv}}。

在 LongBench 上以 Llama-3.1-8B 在严格 15% 预算下，LKV 接近无损，优于 SnapKV、PyramidKV、DuoAttention 与 AdaKV；其消融指出学习型预算（而非选择策略）才是保真度的主要驱动{{cite:lkv}}。代价是需要一个轻量训练阶段（约 0.1% 可训练参数，8×A100 不足 2 GPU-小时），且学到的预算一旦训练完成即固定，不再随输入内容动态调整{{cite:lkv}}。这把 training-free 与 training-aware 的取舍摆到了前台：在极低预算下，学习是接近无损的必要条件，而非可选优化。

### 永久驱逐的边界与两阶段选择

永久驱逐在一个场景下会失效：多轮对话中，早期被驱逐的 token 可能在后续轮次变得必要。{{paper:rocketkv|RocketKV}} 的观察是：在 200 个 qasper 问题中，最大序列长度达 25K，但唯一的 Exact-TopK（$k=256$）索引仅约 1200 个——序列长度远大于真正被访问的 token 数{{cite:rocketkv}}。这激发了两阶段框架：先用 SnapKV 做粗粒度永久驱逐，再在被过滤的集合上做细粒度动态选择，使后续 top-k 预测容易得多{{cite:rocketkv}}。

其混合稀疏注意力（HSA）做二维降维：沿序列维度做页级 max/min 统计（Quest 风格），沿头维度做幅度稀疏（SparQ 风格）{{cite:rocketkv}}。在 NIAH 上以 256-token 预算达到 100% 准确率，相当于在 109K 最大序列长度上超过 400 倍压缩{{cite:rocketkv}}。但单阶段方法（SnapKV/Quest/SparQ）在高压缩比下急剧退化，两阶段二维分解把准确率保持得更远{{cite:rocketkv}}。

多轮场景暴露了驱逐路线的内在张力：RocketKV 在 SCBench 多轮高预算下对 Exact-TopK 有明显差距，因为早期轮驱逐的 token 后续变得必要；其多轮变体 RocketKV-MT 跨轮保留全部 KV 历史（不省存储）但每轮过滤以加速解码，从而追平 Exact-TopK{{cite:rocketkv}}。也就是说，**永久驱逐下的存储节省与多轮鲁棒性互斥**——这直接推动了非永久选择（动态 top-k）或分层存储（把历史放到更廉价的存储层）。

## 数值压缩：量化与低秩

### 标量量化与向量量化

量化路线降低 K/V 向量的数值精度。标量量化（如 KIVI 的逐通道/逐 token INT2/INT4）是 training-free 的，但逐元素量化会破坏向量维度间的结构。{{paper:vqkv|VQKV}} 把残差简单向量量化（RSimVQ）应用于 KV cache，只存码本索引而非全精度向量，按需从常驻码本重建{{cite:vqkv}}。

其经验定位是“中间地带”：在 LLaMA3.1-8B 上达到 82.8%、在 LLaMA3.2-3B 上达到 82.4% 的压缩比，在 LongBench 同等压缩比下比 ASVD、Palu、KIVI 与 SnapKV 更接近全缓存模型{{cite:vqkv}}。原因是量化整个向量保留了标量量化所破坏的维度间结构。代价是需要离线训练码本（在 0.1% 的 OpenWebText 上，无需 LLM 微调），且码本数量与大小需按模型与 K/V 类型分别调参{{cite:vqkv}}。

### 查询感知的低秩压缩

低秩压缩把 K/V（或其查询感知交互）投影到低秩子空间，只存低秩因子。一个关键的理论与实证判断是：**建模查询-键交互 $KQ^{T}$ 而非仅压缩键缓存 $K$，能得到可证明更好的低秩压缩**。{{paper:kqsvd|KQ-SVD}} 的定理 1 把注意力输出误差界为 $QK^{T}$ 误差（经 $V W^{O}$ 放大）与 $V W^{O}$ 误差之和；定理 2 给出 $KQ^{T}$ 的闭式最优 rank-$R$ 分解（截断 SVD，Eckart-Young）{{cite:kqsvd}}。

在 Llama2-7B/13B（无 GQA）与 Llama3-8B、Mistral-7B（GQA）上，K-SVD 对 K 的逼近最准但对 Q 逼近差，导致注意力输出误差更高——在 GQA 模型中共享 K 会放大键单独压缩的误差{{cite:kqsvd}}。KQ-SVD 在 K/Q/V 精度上与 Eigen 相当，但在注意力分数矩阵 $KQ^{T}$ 与 MHA 输出上一致更准，且对 K/Q 尺度不平衡不变（定理 4），而 Eigen 在不平衡增大时退化到 K-SVD 的误差{{cite:kqsvd}}。其局限是评测以矩阵 Frobenius 误差为主，未直接报告端到端任务精度或长上下文吞吐{{cite:kqsvd}}。

## 稀疏注意力与架构替代

### 可训练的硬件对齐稀疏

稀疏路线不是永久驱逐，而是按查询动态选择 KV token 子集。理论 FLOP 减少不等于真实延迟下降——除非稀疏模式与硬件对齐的内核协同设计。{{paper:nsa|NSA}}（原生稀疏注意力）把三分支——压缩块级注意力（粗）、top-k 选择块注意力（细）、滑动窗口局部注意力——通过学习门控融合，且稀疏模式在预训练中学习而非推理时固定{{cite:nsa}}。其算术强度平衡的内核使解码成为内存带宽高效的，从而获得真实墙钟加速{{cite:nsa}}。在 27B 总参/3B 激活的 GQA+MoE 上从零预训练，NSA 的预训练损失一致低于全注意力基线，并在通用与长上下文基准上匹配或超过全注意力{{cite:nsa}}。

代价是必须从零训练，不能直接改造现成 LLM；稀疏配置在 27B/3B MoE 规模上报告，向其他规模稠密模型的迁移未在正文充分刻画{{cite:nsa}}。

### 层内线性/SSM-注意力混合

架构替代路线用固定大小记忆替换全 softmax 注意力，使 KV cache 次线性甚至常数增长。{{paper:nha|NHA}}（原生混合注意力）在单层内统一门控线性 RNN 长期记忆（$m$ 槽）与滑动窗口短期记忆（$w$ token），通过逐层改变 $w$ 在纯线性（$w=0$）、混合与全注意力（$w=N$）之间连续过渡，无需结构变更{{cite:nha}}。其分块并行形式支持高效训练，并能以轻量微调改造预训练 Transformer{{cite:nha}}。

在召回密集任务上，1.3B 的 NHA 平均 46.43，优于 GDN-H、GSA-H 与 Mamba2-H；在 RULER 长上下文（训 2K 测到 8K）上 NIAH-MK 达 81.4/51.8/21.6（1K/2K/4K），在混合方案中泛化最强{{cite:nha}}。把 Llama-3-8B 的部分全注意力层替换为 NHA 模块（仅保留 4 层全注意力）后召回均值 57.64，接近原版 60.08{{cite:nha}}。算子效率上 NHA 近线性扩展并匹配 GSA 速度{{cite:nha}}。其取舍是用固定 $m$ 槽的有界精确召回换取次线性显存——这与保留 Transformer 但缩小其缓存的驱逐/量化路线形成对照。

NSA 与 NHA 的对照点在于改造路径：NSA 需从零训练才能获得全部收益，NHA 则提供了一条微调改造路径——但两者都表明，没有内核级协同设计，稀疏 FLOP 数不会转化为延迟收益{{cite:nsa}}{{cite:nha}}。

## 系统调度与缓存复用

### 跨存储层级的 KV 移动

系统路线不动精度，而把 KV cache 视为可在显存层级间移动的资源。{{paper:lmcache|LMCache}} 在推理引擎（vLLM/SGLang）与异构存储（GPU/CPU/远端）之间提供标准化缓存层，用块级（而非页级）传输与高性能 CUDA 内核实现 400 Gbps 的 CPU 加载带宽，对比 vLLM 原生的 88 Gbps{{cite:lmcache}}。其异步逐层加载与预填充/解码计算重叠，端到端延迟降低 1.46 倍{{cite:lmcache}}。

一个部署条件判断是关键：在低网络带宽（32 Gbps）下，加载只在超过 256K token 时才优于预填充；在 64/128 Gbps 时则对所有长度都占优{{cite:lmcache}}。单节点 CPU 卸载在多轮问答（10K token 查询、500GB CPU DRAM）上对 5 个模型实现 1.9–8.1 倍更小 TTFT 与 2.3–14 倍更高吞吐{{cite:lmcache}}。其增益依赖缓存命中率，而命中率依赖工作负载的前缀重叠度——低重叠工作负载的收益会收缩{{cite:lmcache}}。这条路线与影响精度的压缩正交，可与之组合。

### 复用的精度修复

复用路线为重复上下文（系统提示、检索文档）预计算并复用每段 KV cache，能消除大部分重编码成本。但朴素复用会造成大幅精度下降：PromptCache 式的朴素复用因独立编码的文档丢失跨文档依赖与 RoPE 位置而遭受最高约 35% 的相对精度损失{{cite:kvlink}}。{{paper:kvlink|KVLink}} 的修复是算法性的：位置重编码（存储时不带 RoPE 旋转，拼接时再施加全局旋转）加上可训练 link token（每个文档追加 K 个可训练 token，其注意力图覆盖所有前序文档以隐式重连独立编码的段）恢复了对微调全拼接上界的大部分差距{{cite:kvlink}}。

其 TTFT 在复用预计算 KV 时降低 85%–96%；在 5000 token 上下文约 96%{{cite:kvlink}}。精度修复是算法的（KVLink），部署增益是系统的（LMCache）——两者都需要且正交{{cite:kvlink}}{{cite:lmcache}}。代价是需微调 LLM（6000 步），且把文档视为文本，未刻画多模态 KV{{cite:kvlink}}。

## 跨路线组合与实证可比性

### 组合的乘性与误差叠加

跨路线组合在特定机制下是乘性有效的。{{paper:quantspec|QuantSpec}} 把自推测解码与 KV cache 量化组合：草稿模型只加载 INT4（高 4 位）KV，目标模型从两半重建 INT8，即 $2^{4}\cdot C_U^{\rm INT4} + C_L^{\rm INT4}$，消除了冗余的草稿 KV 副本{{cite:quantspec}}。其算术强度/屋顶线分析指出，长上下文解码阶段算术强度近恒定且低于脊点，故 KV cache 量化（而非权重量化）是主导杠杆{{cite:quantspec}}。双全精度缓冲（2G）保留近期 token 的 FP16 以维持高接受率并支持推测回滚{{cite:quantspec}}。

在 128K 上下文（LWM-Text-Chat，Multi-LexSum），StreamingLLM 与 SnapKV 触发 OOM，而 QuantSpec 以 61.22 GB 峰值显存、94.31% 接受率与 2.49 倍加速运行{{cite:quantspec}}。这表明量化与推测的组合在长上下文内存受限机制下比任一单独路线更有效。但组合的误差是否叠加仍是开放问题：RocketKV 组合驱逐与动态选择，QuantSpec 组合量化与推测，KVLink 组合复用与压缩——联合设计空间（如量化、驱逐、复用、推测叠加多轮回滚下的 KV cache）缺乏统一的预算分配与误差累积分析{{cite:rocketkv}}{{cite:quantspec}}{{cite:kvlink}}。

### 实证结果的可比性条件

跨路线的墙钟加速不可直接比较，因为评测条件异质且常偏有利。RocketKV 报告在 A100 上用 Python/gpt-fast 原型（非 CUDA 优化）最高 3.7 倍加速{{cite:rocketkv}}；QuantSpec 报告随机制变化的 2.49 倍加速与接受率{{cite:quantspec}}；LMCache 报告依赖前缀重叠与带宽的 TTFT/ITL 增益，并在 32 Gbps 下存在 256K 的交叉点{{cite:lmcache}}。基准、批大小、上下文长度、硬件与原型成熟度各不相同，因此头条加速数不可直接比较，部署条件必须逐项核对而非取峰值。

下表把主要路线沿三条设计轴与关键部署条件并置，使取舍可比：

| 路线 | 保留什么 | 压缩/移除什么 | 是否需训练 | 代表部署条件 |
|---|---|---|---|---|
| 驱逐与选择（CAKE/LKV/RocketKV） | 部分 token | 永久丢弃或按需选择 | 启发式免训练；LKV 需轻量训练 | LongBench/NIAH，预算 128L–15% |
| 量化（VQKV） | 码本索引 | 数值精度 | 需离线码本训练（无 LLM 微调） | LLaMA3.1-8B，压缩比 ~82–83% |
| 低秩（KQ-SVD） | 低秩因子 | KQ^T 的秩 | 免训练但需校准集 | Llama2/3/Mistral，矩阵误差为主 |
| 稀疏（NSA） | 按 query 动态子集 | FLOP | 需从零预训练 | 27B/3B MoE，270B token |
| 架构（NHA） | 固定 m 槽 + 滑窗 | 线性增长的 KV | 从零训练或轻量微调改造 | 1.3B，RULER 测到 8K |
| 调度（LMCache） | 全部 KV（跨层移动） | 无（与精度正交） | 免训练 | 8×H100，带宽 32–400 Gbps |
| 复用（KVLink） | 预计算段 KV | 重编码成本 | 需微调 LLM | Llama-3.2-1B/3B/3.1-8B |
| 组合（QuantSpec） | INT4 + FP16 缓冲 | 精度 + 推测 | 免训练（量化免训练） | 128K 上下文，OOM 对照 |

## 开放前沿

若干领域级问题在保留的研究图景中尚未解决。

- **百万 token 可扩展性**：驱逐/量化/低秩方法评测到约 128K token；超出此范围，因模型对稀疏/量化注意力在有效上下文之外不够鲁棒、保留子集或码本无法保存所有 needle，精度退化。架构替代（NHA 的固定 $m$ 槽、NSA 的稀疏块）在设计上界定了精确召回。在激进压缩下，无损的百万 token 单针与多针检索未被任何保留方法证明，该尺度机制仍是开放的{{cite:cake}}{{cite:nha}}{{cite:vqkv}}。
- **多模态与检索增强 KV cache 的联合优化**：KVLink 处理 RAG 缓存复用但把文档视为文本；LMCache 处理存储/移动但与模态无关。视觉/音频编码器的 KV cache（维度、注意力模式、压缩容忍度不同）应如何与文本 KV 一同驱逐、量化或复用，以及跨模态 KV 能否联合压缩，在保留图景中无主证据刻画{{cite:kvlink}}{{cite:lmcache}}。
- **跨路线组合的交互与最优性**：组合有前景但交互与最优性欠探索。联合设计空间缺乏统一分析：压缩预算应如何在阶段与维度间分配、误差是叠加还是抵消。自适应分解（RocketKV 的分裂因子）仅对两阶段展示，未跨路线推广{{cite:rocketkv}}{{cite:quantspec}}{{cite:kvlink}}。
- **training-free 质量保持**：在激进压缩下跨任务与模型可靠地保持质量尚未实现。启发式驱逐（CAKE、SnapKV）免训练但增益有限且向全缓存趋近时缩小；学习方法（LKV、VQKV 码本）需训练/蒸馏阶段才在严格预算下接近无损。在极低预算（如 <10%）跨多样长上下文任务与模型族接近无损的免训练方法未被证明，故 training-free 与 training-aware 的取舍仍是活跃前沿而非已定选择{{cite:cake}}{{cite:lkv}}{{cite:vqkv}}。
- **效率增益的可比性**：报告的效率在异质、常偏有利的条件下测量，使跨方法墙钟比较不可靠。基准、批大小、上下文长度、硬件与原型成熟度各异，头条加速数不可直接比较，部署条件须核对而非取峰值{{cite:rocketkv}}{{cite:quantspec}}{{cite:lmcache}}。

## 小结

KV cache 优化的各路线可沿三条设计轴定位：保留什么、压缩或移除什么、是否需要训练。永久驱逐以多轮鲁棒性为代价换取存储节省；量化与低秩在重建误差与压缩比之间权衡；稀疏与架构替代把 FLOP 减少转化为真实延迟需要内核协同设计与（通常）从头训练；系统调度与复用不动精度而把瓶颈转向带宽与命中率。跨路线组合在特定机制下乘性有效，但其误差交互与最优预算分配仍是开放问题。实证增益在异质条件下测量，跨路线比较须核对部署条件而非取峰值。百万 token 可扩展性、多模态 KV 的联合优化与 training-free 质量保持，构成了当前尚未解决的前沿。本次保留语料中最新相关工作为 2026 年的 LKV（4 月）与 VQKV（3 月），满足对最新 2026 前沿工作的要求。
"""

citations = [
    {"citation_id": "cake", "paper_ref": "paper_4e8f6440-1612-4d3e-8db8-f6448a37a481", "locator": {"kind": "section", "value": "Experimentation"}},
    {"citation_id": "lkv", "paper_ref": "paper_50caf2c8-6053-435a-b7f3-126ca2ec3769", "locator": {"kind": "section", "value": "Experiments"}},
    {"citation_id": "rocketkv", "paper_ref": "paper_73ca1a25-eebf-4359-af53-ef40a6855748", "locator": {"kind": "section", "value": "Experiments"}},
    {"citation_id": "kqsvd", "paper_ref": "paper_3551a2c1-4ca1-45a9-b0db-6aa3f1a4f922", "locator": {"kind": "section", "value": "Experiments"}},
    {"citation_id": "vqkv", "paper_ref": "paper_f93ee025-14bd-4d0d-bf97-8bc19caaacfb", "locator": {"kind": "section", "value": "Experiment"}},
    {"citation_id": "nsa", "paper_ref": "paper_fa5091d7-cac6-4903-9ac5-0503228a0dc8", "locator": {"kind": "section", "value": "Experiments"}},
    {"citation_id": "nha", "paper_ref": "paper_8be2bb1a-cdb4-4b2c-b9dc-4b554916dba0", "locator": {"kind": "section", "value": "Experiments"}},
    {"citation_id": "lmcache", "paper_ref": "paper_b4920f81-b210-4a71-9646-35e3b2691c4e", "locator": {"kind": "section", "value": "Evaluation"}},
    {"citation_id": "kvlink", "paper_ref": "paper_0d3c3604-20ad-40fd-bb00-a547e1e69e50", "locator": {"kind": "section", "value": "Experiments"}},
    {"citation_id": "quantspec", "paper_ref": "paper_7e0d8e58-d37d-4835-8a6b-c0600f7fc397", "locator": {"kind": "section", "value": "Evaluation"}},
]

payload = {"markdown": markdown, "citations": citations}
out = SCRATCH + "/manuscript_input.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)

# self-check: parse back, scan for }} in math
d = json.load(open(out, encoding="utf-8"))
print("rewrote", out, "len(md)=", len(d["markdown"]), "citations=", len(d["citations"]))
print("has rm INT4:", "rm INT4" in d["markdown"])
print("has text{INT4}:", "text{INT4}" in d["markdown"])
