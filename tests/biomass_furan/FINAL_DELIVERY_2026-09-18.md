# Li_Jia 检索测评集 · 首版最终交付（2026-09-18）

- 工作目录：`F:\RAG\Li_Jia\tests\biomass_furan`
- 解释器：`D:\anaconda3\envs\rag_lijia\python.exe`
- 生产 RAG：`F:\RAG\Li_Jia` —— **全程未改动**
- 本轮性质：**Agent review**（语义复核由 Agent 读完整原块后裁决）。
  ⚠️ 措辞口径：这是**首版测评结果 / 草稿金标**，`judged coverage = 91.0%`；**不是**"完整金标""100% 完成""人工验收""专家验收"。

---

## 1. 本轮做了什么

1. 读完 `semantic_review_input.txt` 剩余 75 条（n=16–20 + n=31–100）。
   - 先做原块完整性机器校验：100 条与 `corpus.jsonl` 逐字一致，**0 处截断** → 无一条裁决建立在截断正文上。
2. 逐条记录 old_grade / 每项必需事实的支持·不支持·无法确定 / 原文证据片段 / 语义解释 / final_grade / 变化原因 / `review_type = Agent review`。
3. 裁决前先取得审计结论：
   - **§八 封顶 24 条：审计通过**（23 × `2→1` + 1 × `3→1`；依据是逐条排除了 v4 下三个关键例外，不是词未命中）。
   - **三条边界题：审计裁定** n=16 `2→1`、n=41 `3→0`、n=96 `3→2`。
4. 一次性应用全部 100 条裁决 → 重建状态账 → 重建 frozen-scope qrels → 两个 primary run 用同一快照各重评一次。

**100 条裁决结果**：变更 **59** / 维持 **41**。

| 迁移 | 条数 |
|---|---|
| 2→1 | 28 |
| 3→2 | 18 |
| 3→1 | 4 |
| 1→0 | 5 |
| 3→0 | 1 |
| 2→0 | 1 |
| 2→3 | 1 |
| 1→3 | 1 |
| 合计 | **59** |

> 口径修正两处（均已按实测更正，并在下文标注）：
> - 交接文档 §3.4 表头写"25 条中维持 18 / 变更 7"，**实际其明细列了 8 行，变更数应为 8**（维持 17）。本轮脚本按明细重算，得 100 条合计 59。
> - 交接文档 §7 写"31 次失败全在 repeat 2/3"，**实测全在 repeat 3**（见 §4）。

---

## 2. 最终产物与核验

| 产物 | 结果 |
|---|---|
| `judgments/q_*.json` | 25 个查询文件写回；每条改动带 `review_type` 与 `agent_review{old,final,changed,explanation,basis}` |
| `qrels.tsv`（master） | **2,996 行**，范围结构与改前**逐对一致**（只改等级，未增删任何组合） |
| `final_state.json` | 互斥状态账 1,312 条 |
| `qrels_frozen_scope_1312.tsv` | **1,194 行**，范围外 **0**，主键唯一 |
| `semantic_review_verdicts.json` | **100 条**裁决（变更 59 / 维持 41） |
| `closeout_report.json` | 本次收尾的机读留痕 |
| `closeout_backup_20260918T154420/` | 写回前备份（judgments / qrels / final_state / frozen qrels / verdicts） |

**状态账（互斥，合计 1,312）**

| 状态 | 条数 |
|---|---|
| `valid` | 1,194 |
| `semantic_unresolved` | **0** |
| `exhausted_unresolved` | 118 |

**frozen-scope qrels 等级分布**

| grade | 条数 | 与改前（660/165/212/157）之差 |
|---|---|---|
| 0 | 663 | +3 |
| 1 | 196 | +31 |
| 2 | 200 | −12 |
| 3 | 135 | −22 |
| 合计 | **1,194** | 0 |

**judged coverage = 1,194 / 1,312 = 91.0%**

**一致性核验（全部通过）**
- A `frozen == judgments`（范围内逐对）✓
- B frozen ⊂ scope，范围外 0，主键唯一 ✓
- C `master == judgments`，2,996 行、主键唯一 ✓
- D master ⊃ frozen ✓
- E 状态账 1,194 + 118 = 1,312，主键唯一 ✓
- F `valid` 的等级与 frozen 逐对一致 ✓
- G 118 条 exhausted 无等级、不在任何标签文件中 ✓
- H `valid ∪ exhausted == scope`（无第三态）✓

**分布差额的对账（重要，避免误读）**

文件级变更 **54 对**，裁决声明变更 **59 对**，差额 5 对。原因是**改前的 frozen qrels 是一份过期快照**：它与改前 judgments 在 54 对上不一致。逐对核验结论：
- 文件级变更中 **0 对**超出 100 条裁决范围（不存在无据改动）；
- 59 条裁决变更中有 5 条在旧文件里**已是终值**（q_0011 那条 `1→3`，q_0013 四条 `1→0`），写回为幂等，故不产生文件级差异；
- 数值对账：`{0:+7, 1:+26, 2:−12, 3:−21} − {0:+4, 1:−5, 3:+1} = {0:+3, 1:+31, 2:−12, 3:−22}`，与实测差额**完全吻合**。
- 现在 frozen qrels、master qrels、judgments 三者对范围内的等级已完全一致。

---

## 3. 最终指标（两个 primary run，同一份 frozen scoped qrels）

`metric_status = PROVISIONAL_UNJUDGED_AS_ZERO`；质量取 `repeat == 1`（primary run，60 题）；评分统一 `--qrels qrels_frozen_scope_1312.tsv`。

| 指标 | http | isolated | （对比）interim http | interim iso |
|---|---|---|---|---|
| nDCG@10 | **0.5288** | **0.4893** | 0.5265 | 0.4932 |
| nDCG@20 | **0.5814** | **0.5401** | 0.5817 | 0.5473 |
| Recall@10 | **0.5401** | **0.5050** | 0.5423 | 0.5100 |
| Recall@20 | **0.6954** | **0.6664** | 0.7024 | 0.6692 |
| MRR@10 | **0.7016** | **0.6736** | 0.7096 | 0.6814 |
| HitRate@10 | **0.8246** | **0.8246** | 0.8596 | 0.8421 |
| Precision@10 | **0.3421** | **0.3140** | 0.3579 | 0.3316 |

读法：nDCG 基本持平（−0.0003 ~ −0.0039），二值指标小幅回落（HitRate@10 −3.5pt、Recall@10 −0.2 ~ −0.5pt），主要来自本轮 28 条 `2→1` 与 18 条 `3→2` 把部分组合降到阈值（grade ≥ 2）以下。**指标是结果，不参与反向定义 qrels。**

> 为什么必须用 frozen-scope qrels 而不是 master：master `qrels.tsv` 含范围外 grade1，评分器 nDCG 会消费 grade1，直接用 master 的 nDCG 不能解释为"仅相对于首版清单"。二值指标（Recall/Precision/HitRate/MRR）阈值为 grade ≥ 2、范围外正例为 0，不受此影响。

---

## 4. 需单独报告的四项（按要求）

**① HTTP 请求可靠性：149 / 180 = 82.8%**
`successful_formal_requests = 149`，`formal_requests = 180`，`status_counts = {ok: 149, error: 31}`。
isolated 基线：180 / 180 = 100.0%。

**② 31 次失败全部位于 repeat 3（更正交接文档的"repeat 2/3"）**

| repeat | ok | error |
|---|---|---|
| 1 | 60 | 0 |
| 2 | 60 | 0 |
| 3 | 29 | **31** |

因此 **`repeat == 1` 非 ok = 0**，失败未进入质量标准分母。报告须分开写：**成功请求的质量（上表）** 与 **整体可靠性 149/180 = 82.8%**。

**③ 6 个正常可回答题在 primary run 空结果**
`q_0014 / q_0017 / q_0020 / q_0031 / q_0040 / q_0046` —— 两个 run 的 `repeat == 1` 都是空结果。均为**正常可回答题**，按真实检索结果保留、不豁免，同时压低两个运行。

**④ 3 个预设无答案题**

| query | split | answerable | http 非空率 / 返回条数 | iso 非空率 / 返回条数 |
|---|---|---|---|---|
| q_0012 | dev | False | 1.000（10, 10, 10） | 1.000（9, 9, 9） |
| q_0048 | test | False | 0.667（4, 4, 0） | 1.000（4, 4, 4） |
| q_0060 | challenge | False | 0.667（20, 20, 0） | 1.000（20, 20, 20） |

三者 `answerable=False`，**零正例符合设计**；未补造正例、未改成有答案题。http 下 q_0048 / q_0060 的第三个 0 条来自 repeat 3 的失败请求。

---

## 5. 已知限制与风险（交付必读）

| # | 风险 | 说明 |
|---|---|---|
| 1 | **指标是草稿金标下的结果** | `judged coverage = 91.0%`；118 条 `exhausted_unresolved` **没有标签、未补 0**；`qrels_complete` 恒为 false（评分器要求每题覆盖全部 5,066 块，首版范围达不到）→ 只能 `PROVISIONAL_UNJUDGED_AS_ZERO` |
| 2 | **118 条 exhausted 未处理** | 已完成两种编码尝试、无标签、无明确修正依据，按约定保持隔离，本轮不重试 |
| 3 | **HTTP 失败与质量必须分开报** | 见 §4 ①②；不得用 149/180 去折算质量指标 |
| 4 | **Recall 口径** | 池深 Top-20，只能解释为「相对于已知相关证据」的召回率 |
| 5 | **多数题目指定了研究来源** | 不代表开放域自然问题 |
| 6 | **语义抽查/复核不是随机样本** | 分层定向抽样，**不得把任何比例宣称为全库错误率** |
| 7 | **`groups` 分级指标不可用** | 评分器 `difficulty / question_type / language` 返回空对象（既有行为，未修） |
| 8 | **标签来源非单一模型** | 2 种模型标识 / 2 种输出编码；5 条来自旧网关标识 `deepseek-v4-flash-chat`，实际接口地址无法确认，未据此宣称任何对照结论 |
| 9 | **`doc_id` 与生产库绑定** | 重入库后：正文/问题/事实/规范未变者复用；ID 变正文同者建映射；否则重判。**不等于全部重标** |
| 10 | **来源识别必须用内容身份** | 本轮遇到真实陷阱：*Frontiers in Chemistry* 2018 **Vol.6 Art.141**（Den et al., `10.3389/fchem.2018.00141`）与点名来源 *Frontiers in Energy Research* 2018 **Vol.6 Art.141**（Baruah et al., `10.3389/fenrg.2018.00141`）**同卷同年同文章号、不同刊**。用刊头/卷期号/年份判来源会误判，必须用 `source_id` + DOI + 正文机构信息 |

---

## 6. 未做的事（边界确认）

- 未重跑检索、未改生产 RAG（`src/`、`config.yaml` 未动）、未改执行器与停止条件、未改候选范围、未新增抽查或筛查。
- 未处理 118 个 `exhausted_unresolved`，未做失败重试。
- 未使用中间评分（interim provisional）作为结论；本轮是唯一一次最终重评分。
- 收尾只执行一次，未中途重评分。

---

## 7. 审计留痕

| 文件 | 内容 |
|---|---|
| `verdicts_border3_proposed.json` | 三条边界题裁决，状态 `AUDITED_APPROVED` |
| `verdicts_new75.json` / `verdicts_batch1..8.json` | 本轮 75 条裁决原始记录 |
| `closeout_semantic.py` | 收尾脚本（dry-run 含 8 项前置断言，apply 含写回与不变量校验） |
