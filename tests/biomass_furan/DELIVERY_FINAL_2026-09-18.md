# 最终交付（2026-09-18 15:10）— **首版测评结果 / 草稿金标**

工作目录 `F:\RAG\Li_Jia\tests\biomass_furan`｜**生产 RAG 未改动；未重跑检索；未开启新筛查**

---

## 一、本轮完成的阅读复核：**12 / 100 条**（如实报告，未完成）

`semantic_review_verdicts.json` 逐条含：旧等级、**事实结论（支持/不支持/部分）**、**当前块中的实际证据片段**、**一句语义解释**、最终等级、变化原因。全部标记 `Agent review`。

| # | 组合 | 旧→新 | 事实结论 | 依据（读完整原块） |
|---|---|---|---|---|
| 1 | q_0005 chk_146ef0a2692f27 | 1→1 | 不支持 | 块论双相溶剂萃取，未触及催化剂用量/副反应；同体系同底物、变量为溶剂 → L1a |
| 2 | q_0005 chk_3802f88572be | 1→1 | 不支持 | 块论反应温度影响；同体系、变量为温度 → L1a |
| 3 | q_0005 chk_76e8cf8001bf | 1→1 | 部分 | 对比表含本工作两条：催化剂:木糖 1:20 收率 71.1%，1:40 仅 54.2%——方向支持、机理句缺失 → L1a |
| 4 | q_0005 chk_c637afc692e7 | 1→1 | 部分 | 原文「CrCl3/AlCl3/FeCl3 极低收率可由强副反应加剧解释」触及机理，但对象是其他催化剂 → L1a |
| 5 | q_0005 chk_de8ed6767b30 | 1→1 | 部分 | 配对催化剂段落提到大量不溶腐殖质生成，未及用量机理 → L1a |
| 6 | q_0011 chk_c7b7d600a23b | 1→1 | 不支持 | 环境评价/技术经济评估段，未定义 Bergius–Rheinau；同论文工艺背景 → L1b |
| **7** | **q_0011 chk_e66153b2f5b3** | **1→3** | **支持** | 原文直接给出：「the process developed by the German chemist **Friedrich Bergius** in the 1920s… This process used **highly concentrated aqueous hydrochloric acid (HCl) solutions (40 wt %) at ambient temperatures** to achieve **full lignocellulose hydrolysis**」——**恰好对应必需事实「single low-temperature hydrolysis using fuming HCl」**。旧判 1 级为漏判 |
| 8 | q_0011 chk_f6334e3e233a | 1→1 | 不支持 | HCl/水共沸 + 本研究双相萃取段，未定义该过程 → L1b |
| **9** | **q_0013 chk_09c78331d97d** | **1→0** | **不支持** | 整块为参考文献条目（Rouches/Saini/Sammond…），无组分陈述；且块来源非指定综述 → 规范第四节 |
| **10** | **q_0013 chk_7ccc90b43ddf** | **1→0** | **不支持** | 整块为期刊刊头/题名页，无组分陈述 → 规范第四节 |
| **11** | **q_0013 chk_9824e8bd8361** | **1→0** | **不支持** | 整块为参考文献条目，无组分陈述 |
| **12** | **q_0013 chk_b1d48fe71f8d** | **1→0** | **不支持** | 整块为参考文献条目，无组分陈述 |

**12 条中 7 条维持、5 条变更。** 变更已写回 judgments 与 qrels，留痕 `semantic_review_verdicts.json`。

> **未完成部分：88 条（needs_rejudge 剩 3 + semantic_pending 85）本轮未读。** 阅读材料已备好（`semantic_review_input.txt`，100 条含完整原块，312 KB）。**未对未读条目赋任何新标签**，它们保持在待复核状态。

## 二、状态账（`final_state.json`，组合级互斥，1,312 行）

| 状态 | 数量 |
|---|---|
| `valid`（已通过要求） | **1,106** |
| `needs_rejudge`（规则版本口径，未读） | **3** |
| `semantic_pending`（未完成阅读复核） | **85** |
| `exhausted_unresolved`（两种编码已试尽且无修正依据） | **118** |
| **合计** | **1,312** |

- **已有标签覆盖率 = 1,194 / 1,312 = 91.0%**
- **完成率（valid）= 1,106 / 1,312 = 84.3%**（12 条阅读复核完成后由 83.4% 上升）
- `semantic_unresolved`（读后仍真歧义）：**0** —— 已读的 12 条均能确定，未出现"读完整块仍无法裁决"

## 三、冻结 qrels 快照（`qrels_frozen_scope_1312.tsv`）

| 断言 | 结果 |
|---|---|
| 行数 | **1,194** |
| 范围外组合 | **0** ✅ |
| 主键唯一 | ✅ |
| grade 统计 | 0:**660**｜1:**165**｜2:**212**｜3:**157** |
| judged coverage | **1,194/1,312 = 91.0%** |
| unresolved 未伪造标签 | ✅ |
| master `qrels.tsv` | **保留，未删** |

**快照已采用本轮 5 条修改后的最终等级**（grade 分布相对上一版变化：0 由 656→660、1 由 170→165、3 由 156→157）。

## 四、最终评分（同一快照，两个既有 primary run；未重跑检索）

`metric_status = PROVISIONAL_UNJUDGED_AS_ZERO`

| 指标 | http | isolated |
|---|---|---|
| nDCG@10 | **0.5265** | **0.4932** |
| nDCG@20 | 0.5817 | 0.5473 |
| Recall@10 | 0.5423 | 0.5100 |
| Recall@20 | 0.7024 | 0.6692 |
| MRR@10 | 0.7096 | 0.6814 |
| HitRate@10 | 0.8596 | 0.8421 |
| Precision@10 | 0.3579 | 0.3316 |

**口径与保留项**
- 检索质量：**primary run（`repeat == 1`）**，**60 题**。
- **HTTP 请求可靠性：149 / 180 = 82.8%**；**31 次失败全部位于 repeat 2/3，不进入 primary-run 检索质量分母**。
- **6 个正常可回答题的 primary run 返回空结果**：`q_0014 / q_0017 / q_0020 / q_0031 / q_0040 / q_0046` —— **按真实检索结果保留**，不豁免（召回 0、nDCG 分母照常），同时压低两个运行。
- **3 个预设无答案题单独报告**（不与普通 Recall 混谈）：http 的 q_0048、q_0060 `nonempty_rate = 0.67`；q_0012 = 1.00；isolated 三题均 1.00。

## 五、最终报告措辞（按你的要求）

> **首版测评结果 / 草稿金标，judged coverage = 91.0%（valid 完成率 84.3%）**

**不称**：完整金标｜100% 完成｜人工验收｜专家验收。
语义复核统一注明 **`Agent review`**。

## 六、未解决项

| # | 项 | 数量 |
|---|---|---|
| 1 | **阅读复核未读条目**（needs_rejudge 3 + semantic_pending 85） | **88** |
| 2 | `exhausted_unresolved` | 118 |
| 3 | 评分器 `groups` 分级指标返回空对象 | — |
| 4 | master qrels 的 nDCG 含范围外 grade1（已用快照规避，旧值另存） | — |

本轮未打开新抽查、参数实验、失败重试或生产 RAG 优化。到此停止。
