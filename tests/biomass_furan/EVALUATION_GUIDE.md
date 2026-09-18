# EVALUATION_GUIDE.md —— 检索测评集长期维护手册

- 版本：1.0（2026-09-18）
- 适用：以后**新增文献、更换标注/被测模型、重新测试**时的标准做法
- 本文档是**操作手册**，不是结果报告。首版结果见 `FINAL_DELIVERY_2026-09-18.md`；本次裁决明细见 `semantic_review_verdicts.json`。
- 冻结规范：`ANNOTATION_INSTRUCTIONS.md`（v4）。**改判级规则必须新建版本，不许就地修改本手册引用的规则原文。**

---

## 0. 环境固定约定

| 项 | 值 |
|---|---|
| 项目根 | `F:\RAG\Li_Jia`（**生产 RAG，全程只读**：`src/`、`config.yaml` 不许改，`config.yaml` 被生产服务加载） |
| 测评工作目录 | `F:\RAG\Li_Jia\tests\biomass_furan` |
| 解释器 | `D:\anaconda3\envs\rag_lijia\python.exe`（Python 3.11）。**裸 `python` 指向别的环境，会缺依赖** |
| 测评专用接口配置 | `tests/test_api.yaml`（`base_url=https://api.deepseek.com`，`model=deepseek-flash`，`read_timeout_seconds` 必须等于 `total_timeout_seconds`，`api_key_env=TEST_API_KEY`） |
| 标注凭据 | `config.yaml → chat.api_key_env`（当前 `RAG_CHAT_API_KEY`）。Agent/脚本侧用 `run_full_gapfill.ensure_credential()`：先查环境变量，缺失则用 `winreg` 读 `HKCU\Environment` 注入本进程，**必须在 `resolve_settings()` 之前调用**；不打印、不落盘密钥；**不要用 `reg.exe`**（会被沙箱拦截） |

---

## 1. 测评集结构（以下数字全部实测自文件，2026-09-18）

### 1.1 题目：60 道

| 维度 | 分布 |
|---|---|
| 语言 | zh 42 / en 18 |
| 分区 | dev 12 / test 36 / challenge 12 |
| 难度 | easy 18 / medium 30 / hard 12 |
| 题型 | condition 9 · mechanism 9 · fact 9 · data 6 · definition 6 · comparison 6 · multi_document 6 · paraphrase 3 · no_answer 3 · constraint 3 |
| 可回答性 | answerable 57 / **预设无答案 3**：`q_0012`(dev) `q_0048`(test) `q_0060`(challenge) |
| 点名来源 | 57 题带 `source_group`（3 道无答案题不带） |
| 必需事实 | 全部题目共 74 条 `fact_evidence`（每条含 fact + 判据原文 quote） |

### 1.2 语料

| 项 | 值 |
|---|---|
| `corpus.jsonl` | **5,066 个文本块**（全部 journal_article；doc_id 唯一） |
| `sources.jsonl` | **48 个来源**（status：ready 34 / partial_failed 14） |

### 1.3 判定覆盖（当前）

| 项 | 值 |
|---|---|
| frozen scope（固定候选集） | **1,312** 个 `(query_id, doc_id)` 组合，权威清单 `frozen_scope_1312.json` |
| judged（`valid`，有等级） | **1,194** |
| `exhausted_unresolved`（无标签，冻结隔离） | **118** |
| `semantic_unresolved` | 0 |
| **judged coverage** | **1,194 / 1,312 = 91.0%** |
| frozen 等级分布 | `{0: 663, 1: 196, 2: 200, 3: 135}` |
| master `qrels.tsv` | 2,996 行 = 1,194 范围内 + **1,802 范围外**（其中 grade0 1,160、**grade1 642**；范围外 grade≥2 = 0） |

> `frozen_scope_1312.json` 的口径 = 实际评分 Top-20 并集 ∪ 种子正例 ∪ 该快照时点的已有正例。**复核把正例降级不应让组合退出范围** —— `coverage_scope.py` 读这份固定清单，不重新推导。

---

## 2. qrels 四级定义（v4，冻结）

判级流程两问：**块里有没有必需事实 → 全有=3 / 有一部分=2 / 一项没有 → 第 2 问：有无可说明的辅助价值 → 有=1 / 无=0。**

| 级 | 定义 |
|---|---|
| **3** | 该块**单独**覆盖全部必需事实，并满足硬性限定（指定催化剂/原料/条件/来源）。缺一项即最高 2 |
| **2** | 直接支持**部分**必需事实。判 2 不需要看其他候选块；只要包含某一项必需事实即可 |
| **1** | 有**可说明的辅助价值**，但不直接支持任何必需事实。必须在 reason 里写明属于以下哪一类 |
| **0** | 与该问题无可说明的辅助关系 |

**1 级的五类辅助关系**（缺一不可落到具体某类）：

| 代号 | 类别 | 例 |
|---|---|---|
| L1a | 同体系不同条件 | 同催化剂/溶剂/原料体系，条件或产物不同 → 对照 |
| — | 同原料不同体系 | 同原料，走了不同转化路线 |
| L1b | 同论文的方法或数据来源 | 问题所依据论文的方法段/数据口径 |
| L1d | 直接相关的机理/背景解释 | 解释了现象为什么发生，但不含所求数据 |
| L1c | 不同论文的同类对照实验 | 另一篇论文的可比实验，提供横向对照数值 |

**1 vs 0 的硬边界（判 0 不看关键词命中）**：
- 问题问 A 体系 X 原料收率 → 块讲 A 体系 Y 原料 = 1（同体系不同原料）
- 问糠醛 → 块讲 HMF / CMF / 乙酰丙酸 / FDCA = 0（不同化学品）
- 木聚糖题 → 木糖/葡萄糖的**不同体系**实验 = 0
- 同一论文但与本问题无关的章节 = **0**（"同论文"不是 1 的充分条件）
- 同一研究领域 ≠ 1
- 书目条目 / 刊头 / 致谢 = 0
- 他文转述第三方工作的综述段 = 0（除非明确引述归因）
- 换了催化剂又换体系 = 0
- yield ≠ selectivity ≠ conversion；wt% ≠ mol%；理论预测 ≠ 实测 —— **不能互换**

**引用要求**：判 2/3 必须给块内**连续原文**引用（只可规范化空白，不得改字符、不得跨块借用、不得凭记忆补写；多处引用用省略号分隔）。判 0/1 必须给语义理由，1 级理由必须点明五类中的哪一类。

---

## 3. §八 来源限定规则（v4 新增，高频踩坑点）

**触发**：题目措辞点名具体文献/研究（"2018年《Recent Trends…》综述列出…"、"2021年糖混合物/DES-MIBK研究中…"）。此时来源是**硬性限定**，不是定位提示。

**规则**：
1. **另一篇论文独立陈述了相同事实 → 最高判 1**，不是 2 也不是 3。
   - 不能判 3：别的论文列了三种组分，证明不了指定综述列了那三种。
   - 不能判 2：阈值是 grade ≥ 2；若允许，系统完全没找到指定文献也能命中 HitRate/MRR，与题意不符。
   - **不得因为指标会下降而放宽**——指标是结果，不反向定义 qrels。
2. **例外**：块**明确引述/归因**于指定文献（"as reported in [X]"、作者—年份、标题连续短语 ≥4 词、编号引用**经该文参考文献表解析确认**）。此时按实际覆盖另判 2/3。
3. 同一论文的不同版本/补充材料按实际来源关系识别，不机械比较文件 ID。

**执行检查清单（每条都要过）**：
- [ ] 块内是否把相关事实归因到指定研究？（作者姓氏 / ≥4 词连续标题短语 / 已解析的编号引用）
- [ ] 该块所属论文的**全篇**是否出现过指定来源（标题/DOI）？——全篇不出现则编号引用不可能解析到它
- [ ] 本块所属论文是否**就是**指定研究？（比对 `source_id` + DOI + 正文机构信息，不能只看刊头/卷期/年份）
- ⚠️ 真实陷阱：*Frontiers in Chemistry* 2018 **Vol.6 Art.141**（Den et al., `10.3389/fchem.2018.00141`）与 *Frontiers in Energy Research* 2018 **Vol.6 Art.141**（Baruah et al., `10.3389/fenrg.2018.00141`）**同卷同年同文章号、不同刊**。只认来源必须用 `source_id` + DOI + 正文身份。
- 通用词命中、单词命中、事实相同、"模型声称同文献"都**不构成**归因证据。

---

## 4. Source of truth 清单

| 文件 | 性质 | 说明 |
|---|---|---|
| `ANNOTATION_INSTRUCTIONS.md` | **权威规范** | v4 冻结。改规则须新建版本 |
| `queries.jsonl` / `corpus.jsonl` | **权威数据** | 60 题 + 必需事实判据 / 5,066 块正文。语料内容变更 = 需要重判（见 §6） |
| `frozen_scope_1312.json` | **权威主键** | 1,312 个组合的固定清单。评分/范围判断一律以它为准 |
| `judgments/q_*.json` | **权威标签** | 全量标签（2,996 对）。所有等级的唯一来源 |
| `qrels.tsv` | 派生（保留） | master qrels，2,996 行，由 judgments 全量导出。**勿删**；不得直接用于 frozen nDCG |
| `qrels_frozen_scope_1312.tsv` | **派生，须重生成** | frozen-scope 快照 = judgments ∩ frozen scope。**任何 judgments 变更后必须重新生成** |
| `final_state.json` | 派生，须重算 | 互斥状态账：valid(有等级) / exhausted_unresolved(无标签) / semantic_unresolved |
| `semantic_review_verdicts.json` | 留痕 | 100 条 Agent review 逐条结论（本次复核的全部依据） |
| `semantic_corrections.json` | 受保护台账 | 早期 8 条有完整阅读依据的修正。`merge_gapfill.py` 会保护它，**不许覆盖** |
| `frozen_scope_1312.json` 之外的候选池 | 备用 | `pool.jsonl`（8,252 对）与首版范围**不同源**，不要混用 |
| `results/<run>/runs.jsonl` | 权威原始结果 | 每次检索的原始返回；评分从这里算 |
| `results/<run>/summary_provisional.json` | 派生 | 评分器落盘的最终摘要 |
| `runtime/model_responses/*.json` | 留痕 | 每次标注调用的原始响应（含 finish_reason/usage）；`exhausted_batches()` 从这里推导 |
| `archive-20260917/`、`*_backup_*/` | 历史 | 只读，不要清理 |

**一条铁律**：judgments 是唯一等级来源。**任何派生文件（master qrels、frozen qrels、状态账）在 judgments 变更后都必须重新生成**，否则就是过期快照（本次就踩过：旧 frozen qrels 与 judgments 差 54 对，见 §12）。

---

## 5. 正常测试流程（不新增文献）

```bash
# 工作目录 F:\RAG\Li_Jia
P="D:/anaconda3/envs/rag_lijia/python.exe"

# 1) 评分既有 run（禁止重跑检索，除非明确要新数据点）
$P -B tests/biomass_furan/evaluate.py score \
    tests/biomass_furan/results/http-20260916T063408115501Z \
    --provisional --qrels tests/biomass_furan/qrels_frozen_scope_1312.tsv
# isolated 同理
```

要点：
- `--provisional` 是**必须的**：qrels 不可能完整（见 §9 红线），不加会 blocked。
- `--qrels` 必须指向 **frozen-scope 快照**，不是 master。
- 评分器会把 `summary_provisional.json` 写进 run 目录——这就是该 run 的权威结果文件。

新增一次检索（确实需要时）：
- HTTP 方式：`evaluate.py run`（带 `--base-url`、`--timeout`），服务用 `tests/test_api.yaml` 的配置；
- 隔离方式：`evaluate.py isolated`（离线进程内跑，不经过网络）。
- 运行目录命名带 UTC 时间戳；**重跑会产生新目录，不要覆盖旧 run**。
- 一次 run = formal 3 repeats × 60 题 = 180 请求。质量只看 repeat 1。

---

## 6. 新增文献后的增量标注流程

前提：生产 RAG 的入库/解析/切块属于生产侧；本手册只管**测评侧**怎么接。

1. **入库并导出新语料**
   - 新增文献进入生产索引后，把新块追加进 `corpus.jsonl`（保持 `doc_id/source_id/title/section/page` 字段齐全）。
   - `sources.jsonl` 追加来源行（title、doi、status）。**确认 `source_id` 与生产一致**——来源身份识别永远用 `source_id` + DOI + 正文，不用刊头。
2. **判级主键变化：doc_id 三态处理**（v1 遗留口径，继续沿用）
   - **doc_id 未变、正文/问题/事实/规范未变** → 标签直接复用，不重判；
   - **doc_id 变了但正文相同**（内容哈希一致）→ 建旧→新 doc_id 映射，标签复用；
   - **正文变了**（切块边界、OCR 修正、换解析器）→ 该来源的受影响组合**全部重判**。
   - 映射关系存档（例如 `doc_id_migration.json`），不许只改 jsonl 不留痕。
3. **候选范围**
   - 老题的新块候选 = 该题在新语料上的 Top-20 ∪ 种子正例 ∪ 已有正例，与旧 scope **取并集**后形成新的 frozen 清单（新文件，如 `frozen_scope_<n>.json`）；旧清单保留作历史。
   - **不要**用"当前正例集合实时推导范围"——那是本项目早期返工的根源。
4. **标注执行**（`run_full_gapfill.py`）
   - 每批 5 块、并发 2（`--workers 2`）、`temperature=0`、`max_tokens=12000`、thinking disabled；
   - 编码 `--encoding fallback`：先 compact，失败追加一次 perblock（同块同规则），**不回切不循环**；
   - `MAX_REQUESTS_PER_BATCH = 4`：传输重试与换编码**共用**同一预算，两层不相乘；
   - 停止条件：**连续 10 批** fallback 后仍无产出 → 暂停派发（`CONSECUTIVE_FORMAT_LIMIT`）；**连续 3 次**传输失败 → 暂停（`CONSECUTIVE_NETWORK_LIMIT`）；认证/配置类异常 → 立即停并打印异常类型；
   - **失败隔离**：`exhausted_batches()` 从 `runtime/model_responses` 推导"两种编码都试过且无产出"的批次并自动跳过，重启不清零；`--retry-exhausted` 才强制重试。**不要无限重试失败批次**；
   - 断点：`scan_checkpoints()` 按**唯一 (question, block) 对**判断完成，不是按文件存在或批次级相等；
   - 定点重判批次若与原批次块集相同会被隔离误跳过 → 带 `retask_reason` 的批次已豁免，新增同类逻辑要继承这一点。
5. **合并**：`merge_gapfill.py`（配对级、幂等、自动备份、冲突与修订留痕、保护 `semantic_corrections.json`）。**留痕脚本写回必须 merge 而不是 overwrite**——本项目两次把留痕覆盖成空都是这个原因。
6. **复核**：语义层面的复核按 `make_review_input.py` 的方式出阅读材料（原题/必需事实判据/**完整原块**），Agent 逐条读后裁决。规则定位器（`resolve_section8.py` / `resolve_p4.py`）**只能定位候选，不能出结论**。
7. **收尾**：应用全部裁决 → 重建状态账 → 重生成 frozen qrels → 一次重评分（参考 `closeout_semantic.py` 的断言清单）。

---

## 7. 旧标签复用 vs 必须重判

**可以复用（不重判）**：
- `doc_id` 未变，且块正文、题面、必需事实、判级规范都没变；
- `doc_id` 变了但正文逐字相同（按内容哈希对上），且题面/规范未变 → 建映射后复用；
- 只是**评分口径**变了（例如阈值、指标算法）→ 标签不动；
- 只是**被测模型**换了 → 标签与检索结果都不动（金标与被测对象无关）。

**必须重判**：
- 块正文变了（重切块、解析升级、OCR 修正）——即使语义等价也要过一遍，因为判级依据是"块内原文"；
- 题面、必需事实或判据原文改了；
- 判级规范升级（如 v2→v3 的 2/3 分界、v4 的 §八）→ 受影响等级必须按新规范重新核验（升级发现应升 2/3 的**必须同步升级**，不能只做 0/1 二选一）；
- 该组合从未有过有效标签（unjudged）；
- 旧标签的理由自称 L1x 但等级与之矛盾（等级/理由冲突一律回读原块再定，不能只改字面）。

**灰色情况的处理原则**：拿不准 → 回读完整原块后再定；读完整块仍真歧义 → 标 `semantic_unresolved`，**不强行赋值**。

---

## 8. 如何生成 frozen qrels

任何 judgments 变更后执行（或复用 `closeout_semantic.py` 里的 `write_qrels`）：

```python
# python - <<'PY'
import json, glob
from pathlib import Path
ROOT = Path('.')
scope = set(json.load(open('frozen_scope_1312.json', encoding='utf-8')))
labels = {}
for p in sorted(glob.glob('judgments/q_*.json')):
    qid = json.load(open(p, encoding='utf-8'))['query_id']
    for r in json.load(open(p, encoding='utf-8'))['judgments']:
        labels[(qid, r['doc_id'])] = r['relevance']

rows = sorted((q, d, g) for (q, d), g in labels.items() if f'{q}\t{d}' in scope)
with open('qrels_frozen_scope_1312.tsv', 'w', encoding='utf-8', newline='\n') as fh:
    fh.write('query_id\tdoc_id\trelevance\n')
    for q, d, g in rows:
        fh.write(f'{q}\t{d}\t{g}\n')
# PY
```

**生成后必须断言**（缺一不可）：
1. 行数 = 范围内有标签的组合数（当前 1,194；范围变化后 = 新 scope − 新 exhausted）；
2. **范围外 = 0**；
3. 主键唯一；
4. 与 `judgments` 逐对一致（等级与键都一致）；
5. 与 `final_state.json` 的 `valid` 集合与等级一致；
6. 等级分布变化能用**裁决迁移表**逐条解释（本次实测：文件级变更 54 对 = 裁决 59 对 − 5 对旧快照已达终值）。

---

## 9. 指标怎么解释（含 repeat 与可靠性）

**运行形态**：一次 run = 3 repeats × 60 题 = 180 formal 请求。

**质量分母：只取 `repeat == 1`**（primary run，60 题）。**绝不用"最快的那次"代替 repeat 1。**

**可靠性：全量 180 个 formal 请求**。两者必须分开报告，例如：
> 成功请求质量：nDCG@10 0.5288（http）；整体可靠性 149/180 = 82.8%。
不得用可靠性去折算质量，也不得把失败请求算进质量分母。

**阈值**：二值指标（Recall / Precision / HitRate / MRR）相关判定阈值为 **grade ≥ 2**。
**nDCG**：消费全部等级（含 grade 1），分母用 qrels 里该题的全部等级排序。

**为什么必须用 frozen-scope qrels**：master qrels 含 **642 条范围外 grade1**（范围外 grade≥2 = 0）。直接用 master 算 nDCG，会把这些范围外的 grade1 计入理想 DCG，指标不再能解释为"相对于首版清单"。二值指标因范围外正例为 0 而不受影响。

**怎么读首版结果**（草稿金标口径）：
- 这是 **Agent review 的首版测评结果**，不是"完整金标 / 100% 完成 / 人工验收"；
- `judged coverage = 91.0%`，118 条 exhausted 无标签未补 0 → 只能 `PROVISIONAL_UNJUDGED_AS_ZERO`；
- unjudged 按 0 处理会**系统性压低**指标——方向已知，解释时必须带出；
- Recall 口径：池深 Top-20 → 只能说"相对于已知相关证据的召回"；
- 分级指标（`groups` 的 difficulty/question_type/language）当前返回空对象，不可用（既有行为，未修）；
- 语义复核是**分层定向抽样 + 全量复核指定组合**，不得把任何错误率宣称为全库错误率。

---

## 10. 无答案题怎么处理

- 三道预设无答案题：`q_0012`(dev) / `q_0048`(test) / `q_0060`(challenge)，`answerable=False`。
- **零正例符合设计**：不给它们造正例、不改成有答案题、不因"全 0"而补标签。
- 评分器对它们单独输出 `no_answer` 块（nonempty_rate 与每次返回条数）；解读时只报"是否返回了内容"，不参与质量指标。
- 首版实测：q_0012 http 10/10/10、iso 9/9/9；q_0048 http 4/4/0、iso 4/4/4；q_0060 http 20/20/0、iso 20/20/20。http 下第三个 0 来自 repeat 3 的失败请求。
- 若将来做拒答/答案生成评测，这三题是天然的"应拒答"样本——但**那是新评测，不在本检索测评的范围内**。

---

## 11. 换模型时怎么改

**被测 RAG 换模型（检索/生成）**：
- 金标不动。只产生新的 `results/<新run>/`，用同一 frozen qrels 评分，与旧 run 对比。
- `config.yaml` 是生产配置**不许动**；被测服务的测试配置在 `tests/test_api.yaml`。

**标注（判级）换模型**：
- 允许混用，但每条记录必须带模型标识与编码方式（现有记录已有 2 种模型标识 × 2 种编码）。
- 首版有 5 条来自旧网关标识 `deepseek-v4-flash-chat`，实际接口地址无法确认——**教训：换模型必须把 base_url/model 写进记录**，否则以后无法做对照结论。
- 换模型后先小批试跑（当前配置：`temperature=0`、`max_tokens` 按 `annotation_model_config.json`：first_pass 4096 / review 12000 / adjudication thinking-enabled + low effort 12000），确认解析器（`parse_compact` / `parse_response`）与引用校验（`_quote_supported()` = `match_level ≤ 2`）仍兼容，再放量。
- 引用接受标准散落在多处实现（`verify_judgments.match_level`、`run_full_gapfill.validate()`、`annotate.parse_*`）——**同一标准改一处必须查全部三处**，否则会出现"同批数据一处过一处拒"。
- **同一套判级标准只能有一个权威实现**；发现两处不一致，先查哪处是权威，不要急着回退提示词或调宽批。

---

## 12. 红线：不能做的事

| # | 红线 | 原因 |
|---|---|---|
| 1 | **不许把 unjudged 自动补 0** | 118 条 exhausted 无标签 ≠ 不相关。补 0 是伪造金标，指标虚高且不可撤销 |
| 2 | **不许用 master qrels 直接算 frozen nDCG** | master 含 642 条范围外 grade1，nDCG 分母被污染 |
| 3 | **不许改生产 RAG**（`src/`、`config.yaml`） | config.yaml 被生产服务加载；为提高分数调 RAG 是作弊 |
| 4 | **不许重跑检索来"刷"已有 run** | 每次重跑产生新 run 目录；旧 run 是历史证据，不许覆盖 |
| 5 | **不许把截断正文当完整块判级** | 必须重读完整原块（与 `corpus.jsonl` 逐字比对可机检） |
| 6 | **不许用标题/关键词/数字/单位/正则代替语义判级** | 规则定位器只能定位候选。本项目曾用标题单词匹配误判 27 条、裸数字匹配到刊头行，均已回滚 |
| 7 | **不许就地修改冻结规范** | 改规则必须新建版本，且不追溯旧标签（只核验受影响等级） |
| 8 | **不许让"快照"过期使用** | judgments 变更后，master qrels / frozen qrels / 状态账必须同轮重生成 |
| 9 | **不许无限重试失败批次** | 失败隔离已内置；`--retry-exhausted` 是显式例外，不是默认 |
| 10 | **不许在证据不足时断言根因/错误率** | 抽查比例 ≠ 全库错误率；模型声称 ≠ 归因证据 |
| 11 | **不许把标注密钥打印/落盘** | `ensure_credential()` 只返回状态词 |
| 12 | **不许为指标好看放宽判级** | 指标是结果；§八该降就降 |

---

## 13. 常见坑（本项目实际踩过）

1. **范围外 grade1 污染 nDCG**：master 1,802 行范围外里 grade1 有 642 条。任何 nDCG 必须用 frozen 快照。
2. **旧快照过期**：本次收尾发现旧 `qrels_frozen_scope_1312.tsv` 与 judgments 差 54 对（早期修正只写回了 judgments）。文件级变更 54 对 = 裁决 59 对 − 5 对旧快照已达终值。**每次 judgments 变更必须同轮重生成派生文件**。
3. **截断块**：阅读材料/界面可能截断正文。判级前先与 `corpus.jsonl` 逐字比对；不一致 = 不判。
4. **正则代替语义**：标题单词匹配、裸数字锚点（如 `1`、`10` 能匹配到 "Molecules 2019, 24, 594"）都出过事故。规则定位只用于缩小范围。
5. **失败批次无限重试**：失败隔离从响应留痕推导，重启不清零；误把定点重判当"同批次"跳过时，用 `retask_reason` 豁免。
6. **引用标准多处实现不一致**：曾造成 8 批假失败，按 `match_level ≤ 2` 修正后零内容改动通过。
7. **留痕被覆盖**：写回脚本必须 merge；`semantic_corrections.json` 是受保护台账。
8. **同卷同号不同刊**：Frontiers 2018 Vol.6 Art.141 两篇（见 §3）——来源识别必须用 `source_id`+DOI+正文身份。
9. **数据文件字段为空的来源**：部分 `sources.jsonl` 行无 DOI；身份判断退回 `source_id` + 标题 + 正文自述，三者一致才可用。
10. **shell 里写含反引号的中文 Markdown**：bash 会做命令替换，内容被吃掉。写文件用编辑器工具或 `python - <<'PY'` heredoc。
11. **口径混用**：全量标签（2,996）与范围内标签（1,194）是两个集合；断言里必须写清用的是哪个。
12. **数字要从文件实测，不从交接文档抄**：交接文档曾有"25 条中变更 7"（实为 8）与"31 次失败全在 repeat 2/3"（实为全在 repeat 3）两处笔误。

---

## 14. 命令速查

```bash
P="D:/anaconda3/envs/rag_lijia/python.exe"
cd /f/RAG/Li_Jia

# 评分（唯一入口）
$P -B tests/biomass_furan/evaluate.py score \
   tests/biomass_furan/results/<RUN_DIR> \
   --provisional --qrels tests/biomass_furan/qrels_frozen_scope_1312.tsv

# 范围/覆盖分类（按固定清单）
$P -B tests/biomass_furan/coverage_scope.py

# 机械校验 + 自检
$P -B tests/biomass_furan/verify_judgments.py
$P -B tests/biomass_furan/selfcheck_verify.py

# 标注执行（增量补标）
$P -B tests/biomass_furan/run_full_gapfill.py --plan --workers 2 --encoding fallback
$P -B tests/biomass_furan/run_full_gapfill.py --workers 2 --encoding fallback

# 语义复核阅读材料（复核入口）
$P -B tests/biomass_furan/make_review_input.py

# 收尾（dry-run 带 8 项前置断言；--apply 才写）
$P -B tests/biomass_furan/closeout_semantic.py
$P -B tests/biomass_furan/closeout_semantic.py --apply
```

---

## 15. 数字口径变更记录

| 日期 | 变更 |
|---|---|
| 2026-09-18 | 首版收尾：100 条 Agent review 全部应用（变更 59 / 维持 41）；状态账 valid 1,194 / exhausted 118 / unresolved 0；frozen qrels 1,194 行，分布 {0:663, 1:196, 2:200, 3:135}；judged coverage 91.0%；master 2,996 行范围结构未变 |
| 2026-09-18 | 更正交接文档两处笔误：①"25 条中变更 7"→ 8（明细本身列 8 行）；②"31 次失败全在 repeat 2/3"→ 全在 repeat 3（repeat1/2 各 60 ok，repeat3 29 ok + 31 error） |

> 以后每次影响结构数字的操作（改判级、重生成 qrels、新增文献）都在本节追加一行。
