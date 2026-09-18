# HANDOFF_NEXT_2026-09-18 — Li_Jia 检索测评集（交接给下一轮对话）

> ⚠️ **本文件取代 `HANDOFF_CURRENT.md`（后者为 2026-09-18 10:20 的旧版，已过时，只作历史）。**
> ⚠️ 状态快照为 **2026-09-18 15:05**。若更晚执行过操作，先核对数字再动手。

- **工作目录**：`F:\RAG\Li_Jia\tests\biomass_furan`
- **解释器（必须绝对路径）**：`D:\anaconda3\envs\rag_lijia\python.exe`（Python 3.11）
  > **裸 `python` 不可用** —— 指向 WorkBuddy 托管环境，无项目依赖。
- **生产 RAG**：`F:\RAG\Li_Jia`（`src/`、`config.yaml`）—— **全程未改动，今后也不要改**

---

## 0. 一句话现状

首版固定候选集 **1,312** 个 `(query_id, doc_id)` 组合中，**25 条已完成 Agent 阅读复核**，**75 条未读**。两个既有检索运行已能用冻结快照评分，但指标是 **intermediate provisional**。
**唯一待办：读完剩下 75 条 → 一次性收尾。**

---

## 1. 目标与边界

**目标**：构建可增量更新的检索测评集（带金标 qrels），并用**已有检索运行记录**完成测评。**不重跑检索、不做答案生成评测。**

| 允许 | 不允许 |
|---|---|
| 完善题库、规范、候选池、qrels | 改生产解析/切块/索引/检索/融合/重排/答案生成/拒答 |
| 优化标注拆分、并发、断点、复核流程 | 改生产提示词、默认配置、模型、服务架构 |
| 建/改独立测评脚本 | 为提高分数调整 RAG |
| 用现有接口跑检索测试 | 扩展为答案生成质量评测 |

**`config.yaml` 被生产服务加载 → 不得修改。** 测评专用接口配置在 `tests/test_api.yaml`。

---

## 2. 凭据通道（已打通，不要再问用户）

**根因**：用户在 PowerShell 里 `$env:RAG_CHAT_API_KEY=...` 只存在于那个进程树；Agent 的 shell 是另一棵进程树，**永远继承不到**。这是进程隔离，不是权限问题。

**已实现方案 A**（`run_full_gapfill.py: ensure_credential()`）：
1. 先查 `os.getenv(api_key_env)`（变量名从 `config.yaml: chat.api_key_env` 读，不硬编码）
2. 缺失时读 `HKCU\Environment`（`setx` 写入处），注入**本进程** `os.environ`
3. 只返回状态词 `env / registry / absent / unreadable`；**密钥值不打印、不写日志、不落盘**

**用户已执行过一次** `setx RAG_CHAT_API_KEY "<key>"`；实测 `ensure_credential()` 返回 **`registry`**，Agent 已能自行发起调用。**不需要重启 WorkBuddy。**

⚠️ 两个坑：
- **`ensure_credential()` 必须在 `resolve_settings()` 之前调用**，否则 `Settings.load()` 里 `chat_api_key=None`，`'Bearer '+None` 抛 **TypeError**（曾因此白跑 84 次请求）。
- **不要用 `reg.exe`**（被沙箱黑名单拦截）—— 用 Python `winreg`。

---

## 3. 当前进度（精确数字）

### 3.1 数据基础

| 项 | 数量 |
|---|---|
| 论文 | 51 篇（可检索 48）｜页数 929｜文本块 5,444（可检索 5,066） |
| 题目 | 60 道（中文 42 / 英文 18；简单 18 / 中等 30 / 困难 12；开发 12 / 测试 36 / 挑战 12） |

### 3.2 候选集（**已冻结**）

**主键 = `frozen_scope_1312.json`，共 1,312 个组合。**
口径 = 实际评分 Top-20 并集 ∪ 种子正例 ∪ 该快照时点的已有正例。
`coverage_scope.py` 现在**读这个固定清单**，不再用"当前正例集合"实时重定义范围——复核把正例降级**不应让组合退出已承诺的范围**。

### 3.3 状态账（`final_state.json`）

| 状态 | 上次重建时 | **读完 25 条后应为** |
|---|---|---|
| `valid` | 1,106 | **1,119** |
| `needs_rejudge` | 3 | **0** |
| `semantic_pending` | 85 | **75** |
| `exhausted_unresolved` | 118 | 118 |
| 合计 | 1,312 | 1,312 |

- **已有标签覆盖率 = 1,194 / 1,312 = 91.0%**（仅此含义）
- ⚠️ **`final_state.json` 尚未按 25 条复核结果重建**，需重算（见 §6）

### 3.4 语义复核进度（**核心待办**）

| 组 | 总数 | 已完成 | 未读 |
|---|---|---|---|
| `needs_rejudge`（规则版本口径） | 15 | **15** | 0 |
| `semantic_pending` | 85 | **10** | **75** |
| 合计 | 100 | **25** | **75** |

**已完成的 25 条：维持 18 / 变更 7**（逐条结论在 `semantic_review_verdicts.json`）

| 变更 | 说明 |
|---|---|
| `q_0011 chk_e66153b2f5b3` 1→**3** | 块正文逐字给出 Bergius 工艺定义（fuming HCl / low temperature / one single step）→ 旧判 1 级是**漏判** |
| `q_0013` ×5（chk_09c78331d97d / chk_7ccc90b43ddf / chk_9824e8bd8361 / chk_b1d48fe71f8d / chk_e13189c5ea57）1→**0** | 整块为参考文献条目或期刊刊头 → 规范第四节无实质内容 |
| `q_0026 chk_3502598feddaf5` 2→**1** | 只讲循环回用，8 wt% 酸浓度与 2-MTHF 等体积比全缺 |
| `q_0035 chk_df3aec9b2824dc` 2→**1** | 给的是 FeCl3 59.9% / H2SO4 45.7%，非所问 55.9% / 73.6% |

**7 条变更已写回 `judgments/q_*.json` 与 master `qrels.tsv`。** 备份：`judgments_backup_review_*`、`judgments_backup_review2_*`。

### 3.5 冻结评分快照

`qrels_frozen_scope_1312.tsv` —— **当前是上一版（25 条结论尚未反映）**：
1,194 行｜范围外 0｜主键唯一｜grade {0:660, 1:165, 2:212, 3:157}｜judged coverage 91.0%。**须在 75 条读完后重新生成。**

### 3.6 指标（**intermediate provisional，勿当最终结果引用**）

`metric_status = PROVISIONAL_UNJUDGED_AS_ZERO`｜质量取 **`repeat == 1`（primary run），60 题**

| 指标 | http | isolated |
|---|---|---|
| nDCG@10 | 0.5265 | 0.4932 |
| nDCG@20 | 0.5817 | 0.5473 |
| Recall@10 | 0.5423 | 0.5100 |
| Recall@20 | 0.7024 | 0.6692 |
| MRR@10 | 0.7096 | 0.6814 |
| HitRate@10 | 0.8596 | 0.8421 |
| Precision@10 | 0.3579 | 0.3316 |

---

## 4. 关键决策与实现要点（含踩过的坑）

### 4.1 判定编码与 fallback 阶梯

- 两种编码：`compact`（位数串）与 `perblock`（逐块证据对象）
- `--encoding fallback`：先 compact；**失败时最多追加一次 perblock**（同块、同规则），**不回切、不循环**
- **每批请求上限 `MAX_REQUESTS_PER_BATCH = 4`**，传输重试与换编码**共用同一预算**，两层不相乘
- 分层计数 `requests_ok` / `requests_format_reject` / `requests_transport_error` / `requests_encoding_switch`：
  **前三项互斥相加 = 总请求数；换编码是另一维度，不与前三项相加。**

### 4.2 停止条件（当前版本）

| 情况 | 行为 |
|---|---|
| 单批 compact → 1 次 perblock 后仍不合格 | **进入失败清单，继续下一批**（失败隔离） |
| 认证/配置/程序异常（非 URLError/TimeoutError/OSError） | **立即停止**并打印异常类型 |
| 连续 3 次传输请求失败 | 暂停 |
| **连续 10 个批次最终都无有效产出** | 暂停派发并汇报 |
| 跨运行 | **失败隔离内置**：`exhausted_batches()` 从 `runtime/model_responses` 推导"两种编码都试过且无产出"的批次并自动跳过，**不因重启清零**；`--retry-exhausted` 强制 |

⚠️ 定点重判批次与原批次块集相同，会被隔离误跳过 → 已豁免携带 `retask_reason` 的批次。

### 4.3 ⚠️ 最重要的代码修正：引文接受标准

规范 `ANNOTATION_INSTRUCTIONS.md` §六.1 明文**允许省略号分段引文**（"多处引用用省略号分隔，每段仍须是该块内连续原文"）。
`verify_judgments.match_level`（L1 字面 / L2 空白）与 `run_full_gapfill.validate()` 都按 `match_level ≤ 2` 实现，**只有 `annotate.parse_*` 用整串连续匹配** → 造成 8 批假失败。
已改为 `_quote_supported()` = `match_level ≤ 2`，**8 批零内容改动即通过**。
> 教训：**同一套判定标准散落在多处实现时，先查哪一处是权威，别急着回退提示词或调批宽。**

### 4.4 其他必须记住的坑

1. **留痕脚本写入必须 merge 而非 overwrite**。`apply_semantic_corrections.py` 与 `merge_gapfill.py` 都曾把留痕覆盖成空，已加保护；**新写这类"从当前状态重新推导"的脚本时务必注意**。
2. **`run_full_state.json` 的 token 只统计成功请求** → 实际消耗曾被低估 2.1 倍。新执行器已把被拒响应的 usage 计入，无用量请求单列"未知"。
3. **`stop.set()` 后，在飞批次可能成功并重置 `consecutive_format_fail`** → 状态文件里看不出是停止条件触发的。已补 `stop_reason` 埋点。
4. **`pending` 按索引顺序** → 已知坏批次排在最前；**不带 `--exclude` 会立刻触发停止条件**。
5. **评分器不做范围过滤，且 nDCG 用 `qrels.get(did,0)` 全部等级（含 grade 1）**。master `qrels.tsv` 有 **642 条范围外 grade1** → **master 下的 nDCG 不能称"仅相对于首版清单"**。已加 `--qrels` 参数指向冻结快照规避。二值指标（Recall/Precision/HitRate/MRR）阈值为 **grade ≥ 2**，范围外正例为 0，故不受影响。
6. **6 个 `repeat==1` 空结果**：`q_0014 / q_0017 / q_0020 / q_0031 / q_0040 / q_0046` —— **全部是正常可回答题**，按真实检索结果保留，不豁免，会同时压低两个运行。
7. **规范 §八 的正确判法**：`source_group` 差异 + 理由无归因**不足以**证明应降级。必须确认**块正文明确把相关事实归因到指定研究**（作者—年份、标题**连续短语 ≥4 词**、或编号引用**经该文参考文献表解析确认**）。通用词、单词命中、事实相同、模型声称"同文献"**都不算证据**。（曾用标题单词匹配误判 27 条，已全部回滚。）
8. **P4 的正确判法**：判 3 级要求**单独覆盖全部必需事实**。但**引文未命中只是定位信号**——事实可换措辞表达；**数值/单位只能找候选片段，不能直接证明支持**；必须确认对象、变量、条件与结论对应。（曾用裸数字 `1`/`10` 当锚点，匹配到 "Molecules 2019, 24, 594"，已收紧。）
9. **输出被截断的材料一律不判** —— 必须重读完整块。曾把 520 字符截断当"完整阅读"。

---

## 5. 文件清单与核心逻辑

### 5.1 数据与产物

| 文件 | 内容 |
|---|---|
| `queries.jsonl` / `corpus.jsonl` | 60 题＋必需事实 / 语料块正文（5,444） |
| `pool.jsonl` | 深候选池 8,252 对（备用；与首版范围**不同源**） |
| `seed_judgments.jsonl` | 种子正例（99 对进入首版范围） |
| **`frozen_scope_1312.json`** | **首版固定清单（权威主键）** |
| **`qrels.tsv`** | **master qrels，2,996 行，勿删** |
| **`qrels_frozen_scope_1312.tsv`** | **冻结评分快照（需重生成）** |
| `judgments/q_*.json` | 正式标签（60 题） |
| `final_state.json` | 组合级互斥状态账（需重建） |
| `final_todo.json` / `np_no_label.json` / `needs_rejudge_pairs.json` | 唯一待办 / 118 条无标签 / 15 条规则版本待复核（已读完） |
| **`semantic_review_input.txt`** | **100 条阅读材料（含完整原块，312 KB）—— 下一轮的输入** |
| `semantic_review_index.json` | 100 条的编号与身份（写回用） |
| **`semantic_review_verdicts.json`** | **已完成 25 条的逐条结论** |
| `semantic_corrections.json` | 语义修正台账（8 条有完整阅读依据，受保护） |
| `semantic_pending_review.json` | 待复核清单 |
| `gapfill/_gapfill_index.json` | 原批次索引（275 批 / 1,250 块） |
| `gapfill/_gapfill_supplement.json` | 补漏批次 + 定点重判批次（runner 一并读取） |
| `runtime/model_responses/*.json` | **每次调用的原始响应（含 finish_reason / usage）** |
| `results/http-20260916T063408115501Z` | HTTP 运行：formal 180 = **149 ok + 31 error（全 URLError）** |
| `results/isolated-20260916T064442079079Z` | 隔离基线：formal 180 = **180 ok** |
| `archive-20260917/` | 旧规范、Agent 时代任务文件与原始 judgments |

### 5.2 脚本

| 脚本 | 作用 |
|---|---|
| `run_full_gapfill.py` | **主执行器**：`--plan` / `--workers` / `--encoding` / `--exclude` / `--retry-exhausted`；含 `ensure_credential()` / `exhausted_batches()` / `scan_checkpoints()`（配对级断点） |
| `annotate.py` | 提示词构建、`parse_compact` / `parse_response`、`_quote_supported()`、`request_batch(..., encoding, note)` |
| `verify_judgments.py` | 机械校验（`match_level` 四层）＋ `selfcheck_verify.py`（14 断言） |
| `evaluate.py` | 评分器：`score <run_dir> [--provisional] [--qrels <path>]` |
| `coverage_scope.py` | 按**固定清单**分类 scope / reusable / review_l1 / review_l23 / unlabelled |
| `merge_gapfill.py` | 正式合并（配对级、幂等、带备份、冲突与修订留痕、**保护语义修正**） |
| `make_retask_batches.py` | 为"两种编码都失败"的批次生成**只含受影响块**＋带失败原因的定点重判任务 |
| `repair_failed_batches.py` | 失败批次三分类 + 仅机械修复（R1 字形引文还原），带 before/after 留痕 |
| **`make_review_input.py`** | **生成 100 条阅读材料（下一轮从这里开始）** |
| `resolve_section8.py` / `resolve_p4.py` | ⚠️ **规则定位器**：只用于**定位候选**，结论**不得**直接当语义判定（曾据此误判 32 条并已回滚） |
| `diag_parse_failures.py` | 重放解析器做失败分类（只看格式，**不能替代语义复核**） |
| `build_provenance.py` | 来源台账 `gapfill/_provenance.json` |
| `make_supplement_task.py` | 为清单内但无批次覆盖的组合生成补漏任务（空闲编号，不重编号） |

---

## 6. 待办事项与后续计划

### ★ 唯一要做的事：读完剩余 75 条

**输入已备好**：`semantic_review_input.txt`（100 条，含完整原块）＋ `semantic_review_index.json`。
**已完成的 25 条**在 `semantic_review_verdicts.json`，**跳过即可**。

对每条读：1. 原题　2. 必需事实　3. 硬性来源条件　4. **当前 doc_id 的完整原块**　5. 必要时仅为解析引用身份查看该论文参考文献

逐条记录：
```
old_grade
每项 required fact / hard constraint 的支持 / 不支持 / 无法确定
实际原文证据片段（引用原文）
一句语义解释
final_grade
若变化：old → new 及原因
review_type = "Agent review"
```
**纪律**：不得用截断正文裁决｜不得用标题/关键词/数字/单位/正则替代语义理解｜§八 必须确认事实确实归因于指定研究｜P4 必须确认对象、变量、条件与结论对应｜**读完整块仍真歧义才标 `semantic_unresolved`，不强行赋值**。

### 全部读完后，一次性收尾（**在此之前不要做**）

```powershell
# 工作目录 F:\RAG\Li_Jia
$P = "D:\anaconda3\envs\rag_lijia\python.exe"

# 1) 应用全部语义修正到 judgments / master qrels
#    （参考 semantic_review_verdicts.json 的结构与既有写回逻辑）
# 2) 重建互斥状态账 final_state.json（valid / semantic_unresolved / exhausted_unresolved）
# 3) 重新生成 qrels_frozen_scope_1312.tsv
#    断言：只含 frozen_scope_1312 内组合；范围外=0；主键唯一；unresolved 不伪造标签；master qrels 保留
# 4) 用同一快照重新评分两个既有 primary run
& $P -B tests\biomass_furan\evaluate.py score tests\biomass_furan\results\http-20260916T063408115501Z     --provisional --qrels tests\biomass_furan\qrels_frozen_scope_1312.tsv
& $P -B tests\biomass_furan\evaluate.py score tests\biomass_furan\results\isolated-20260916T064442079079Z --provisional --qrels tests\biomass_furan\qrels_frozen_scope_1312.tsv
# 5) 输出最终 grade 0/1/2/3 分布 + judged coverage
# 6) 生成最终交付文档
```

**不重跑检索。不开启新的全量筛查或无限重试。**

### 后续仍要处理（不建议本轮做）

- **`exhausted_unresolved` 118 条**：已完成两种编码尝试、**没有标签**（未补 0）、**无明确修正依据** → 保持隔离
- **评分器 `groups` 的 difficulty / question_type / language 返回空对象** → 分级指标不可用（既有行为，未改）

---

## 7. 已知问题与风险点

| # | 风险 | 说明 |
|---|---|---|
| 1 | **指标是草稿** | 75 条未读；**118 条 `exhausted_unresolved` 没有标签、未补 0**；**75 条语义待复核组合暂沿用既有标签进入 provisional qrels**。因此 0.5265 / 0.4932 是 **intermediate provisional，不是最终语义验收后的结果** |
| 2 | **措辞红线** | 不得称"完整金标 / 100% 完成 / 人工验收 / 专家验收"。统一写 **Agent review**。正确表述：**首版测评结果 / 草稿金标，judged coverage = 实际比例** |
| 3 | **`qrels_complete` 永远为 false** | 评分器要求每题覆盖全部 5,066 块，首版范围达不到 → 只能 `PROVISIONAL_UNJUDGED_AS_ZERO` |
| 4 | **Recall / nDCG 口径** | 评分器无范围过滤。因**范围外正例 = 0**，二值指标可说"相对于已知相关证据"；但 **nDCG 消费 grade1（含 642 条范围外）**，必须用冻结快照，不能沿用 master 的 nDCG |
| 5 | **HTTP 31 次失败** | 全在 **repeat 2/3**；`repeat==1` 非 ok = 0 → **未进入质量标准分母**。报告须分开写：**成功请求质量** vs **整体可靠性 149/180 = 82.8%** |
| 6 | **语义抽查不是随机样本** | 分层定向抽样，**不得把任何比例宣称为全库错误率** |
| 7 | **规则定位器不可当判定用** | `resolve_section8.py` / `resolve_p4.py` 曾产出 32 条错误结论并已回滚，**只用于定位候选** |
| 8 | **标签来源非单一模型** | 2 种模型标识 / 2 种输出编码（允许）；5 条来自旧网关标识 `deepseek-v4-flash-chat`，**实际接口地址无法确认**，未据此宣称任何对照结论 |
| 9 | **`doc_id` 与生产库绑定** | 重入库后：正文/问题/事实/规范未变者复用；ID 变正文同者建映射；否则重判。**不等于全部重标** |
| 10 | **池深 Top-20** | Recall 只能解释为「相对于已知相关证据」的召回率 |
| 11 | **多数题目指定了研究来源** | 不代表开放域自然问题 |
| 12 | **`verify_judgments` 的 `extra_id`** | 3 条（q_0014 / q_0020 / q_0047）不在 `pool.jsonl` 内 —— 已确认**都在冻结语料中，且都通过"种子正例"进入首版范围**（两套口径不同源），**非缺陷** |

---

## 8. 沿用规范与常量

- **规范**：`ANNOTATION_INSTRUCTIONS.md`（**v4，已冻结**）。等级：**3**=单独覆盖全部必需事实+硬性限定；**2**=直接支持部分必需事实；**1**=可说明的辅助价值（须归入 5 类：L1a 同体系不同条件 / L1b 同论文方法数据基础 / L1c 他文同类对照 / L1d 机理背景）；**0**=无辅助关系。
- **关键边界**：同论文本身不足以判 1；仅同物质名不足以判 1；同论文但话题无关→0；同时换催化剂与体系→0；他文转述第三方工作的综述段→0；书目条目/刊头/致谢→0。
- **题目点名来源时**：他文独立陈述相同事实**最高判 1**；例外是块**明确引述或归因**于该文献。
- **预设无答案题**：`q_0012`（dev）/ `q_0048`（test）/ `q_0060`（challenge），`answerable=False`，**零正例符合设计，不得补造正例、不得改成有答案题**。
- **模型参数**：`deepseek-flash` @ `https://api.deepseek.com`；`max_tokens=4096`；`temperature=0`；`thinking=disabled`；每批 5 块、并发 2。
- **累计用量**（最后一次统计）：336 条响应 / **1,437,083 tokens**（prompt 1,347,810 + completion 89,273），无用量 0 条。

---

## 9. 下一轮的开场提示词（可直接粘贴）

```
继续 Li_Jia RAG 检索测评集项目。工作目录 F:\RAG\Li_Jia\tests\biomass_furan。
解释器固定用 D:\anaconda3\envs\rag_lijia\python.exe（裸 python 不可用）。
先完整读 HANDOFF_NEXT_2026-09-18.md（它取代旧的 HANDOFF_CURRENT.md），再执行。
凭据通道已打通（run_full_gapfill.ensure_credential 读 HKCU\Environment），不要再问用户凭据。
不要修改生产 RAG，不要重跑检索，不要开新的全量筛查。

当前唯一任务：读完 semantic_review_input.txt 里剩余 75 条
（semantic_review_verdicts.json 已含 25 条，跳过）。
严格按交接文档第 6 节要求逐条阅读：原题 / 必需事实 / 硬性来源条件 / 完整原块，
逐项写 支持·不支持·无法确定 + 实际原文证据片段 + 一句语义解释 + final_grade，
review_type=Agent review，并记录 old → new 及原因。
不得用截断文本、标题匹配、关键词、数字单位或正则替代语义理解。
读完整块仍真歧义才标 semantic_unresolved。
全部读完后一次性收尾：应用修正 → 重建互斥状态账 → 重生成 qrels_frozen_scope_1312.tsv
→ 用同一快照重新评分两个既有 primary run → 出最终 grade 分布与 judged coverage → 最终交付文档。
```
