# 最终报告（2026-09-18 11:55）

工作目录 `F:\RAG\Li_Jia\tests\biomass_furan`｜解释器 `D:\anaconda3\envs\rag_lijia\python.exe`
**生产 RAG（`src/`、`config.yaml`）全程未改动。** 本轮无 API 调用。

---

## 1. 范围完成情况

| 项 | 数量 |
|---|---|
| 首版固定清单 | **1,312**（`frozen_scope_1312.json`） |
| **已有标签覆盖率** | **975 / 1,312 = 74.3%** |
| 其中待复核 1 级 / 2-3 级（规则版本） | 24 / 2 = 26 |
| **净可复用** | **949** |
| 未见标签 | **337** |
| 验收缺口（待复核 + 未标） | **363** |
| 增补候选（不并入首版） | 0 |

> **975 不等于「已验收」。** 其中 26 条按规则版本待复核，另有 **82 条语义待裁决**（全部落在这 975 之内），两者口径不同、按配对各自可查。

**待执行 = 363 条**，由两部分组成：

| 来源 | 批次 | 条数 |
|---|---|---|
| 原任务清单待执行 | 77 | 362 |
| **补漏任务**（`q_0060_batch_08`，独立编号、不改原索引、不覆盖原批次） | 1 | **1** |
| **合计** | **78** | **363** |

补漏说明：清单内有 **62** 个组合不在任何原批次里，其中 **61 个已有标签**、**1 个缺标签**（`q_0060/chk_c189cd855d6d974fa4f8ee42`）——这就是 363 与 362 的 +1。

## 2. 等待用户执行命令

标注执行需要 `RAG_CHAT_API_KEY`，**本进程不可读**。**等待用户执行**（工作目录 `F:\RAG\Li_Jia`）：

```powershell
# ① 从未尝试的 56 批 / 263 条
D:\anaconda3\envs\rag_lijia\python.exe -B tests\biomass_furan\run_full_gapfill.py --workers 2 --encoding fallback --exclude tests\biomass_furan\run_full_rejudge_list.json
# ② 21 批定点重判 / 99 条
D:\anaconda3\envs\rag_lijia\python.exe -B tests\biomass_furan\run_full_gapfill.py --workers 2 --encoding fallback --exclude tests\biomass_furan\run_full_never_attempted_list.json
# ③ 补漏 1 条（已在待执行队列，无需排除清单）
D:\anaconda3\envs\rag_lijia\python.exe -B tests\biomass_furan\run_full_gapfill.py --workers 2 --encoding fallback
```
参数、断点、每批请求上限 4、两条停止条件均沿用；跨轮尝试次数落 `run_full_attempts.json`。

## 3. 语义待定项

**已确定修正 8 条**（`semantic_corrections.json`，`merge_gapfill.py` 已加保护、重新合并不覆盖）：
q_0042 五条 3→2（2 项必需事实只支撑第 2 项）｜q_0004 / q_0007 / q_0008 各 1 条 2→1。

**⚠️ 已回滚 27 条 §八 修正。** 我原先按"题目点名来源 + `source_group` 不同 + 理由无归因"把 27 条 2/3 级封顶为 1。复核依据时发现：**我的"归因"检查无效**——它匹配的是 `biomass` / `lignocellulosic` / `pretreatment` 这类同领域通用词，任何相关论文标题里都有，因此不能证明块内是否有引述/归因。已用修正前快照还原原等级，转入待裁决。
**已确认成立的部分**：4 道题的题面**确实点名了具体来源**（q_0013/q_0019 点到综述标题，q_0027/q_0029 以"2020年Eucalyptus grandis研究""2021年ChCl/GA DES实验"限定）。
**未验证的部分**：块内是否明确引述/归因该文献。需要读原文裁决。

**待定 107 条 → 按（题目,块）去重 104 条 → 处置结果**（`semantic_pending_resolution.json`）：

| 结果 | 条数 |
|---|---|
| 已修正 | 6 |
| **原判正确**（理由措辞松，等级无其他证据推翻；同论文/同体系的"数据基础"本身可以是 2 级直接证据） | 16 |
| **交用户裁决** | **82**（P4 三级但必需事实原文未全命中 55｜§八 归因未验证 27） |

**P4 只用于定位**：必需事实可以换措辞表达，原文命中不足**不作为降级依据**。**待定清单处理完毕，不自动开启新一轮全量筛查。**

## 4. 指标

```
metric_status = blocked_incomplete_qrels
ValueError: Answerable query without positive evidence
```
原因：q_0051–q_0059 共 9 题仍零标签（属第 2 节待执行范围）。**不强行出 provisional 数字，不写成语义验收通过。** 跑完 3 条命令后仍需人工裁决 §3 的 82 条。

当前可算的诊断量：

| 口径 | 值 |
|---|---|
| 清单覆盖率 | 74.3% |
| http 实际返回项已判断比例 | 73.3%（660/901） |
| isolated 实际返回项已判断比例 | 72.6%（679/935） |

| 运行目录 | formal | ok | error |
|---|---|---|---|
| http-20260916T063408115501Z | 180 | **149** | **31（全为 URLError）** |
| isolated-20260916T064442079079Z | 180 | **180** | 0 |

## 5. 累计耗时与用量

| 项 | 值 |
|---|---|
| 累计响应 | **336** 条（无用量 **0** 条） |
| 累计 tokens | prompt **1,347,810** + completion **89,273** = **1,437,083** |
| — `deepseek-flash`（官方 API） | 331 条｜1,406,010 tokens |
| — `deepseek-v4-flash-chat`（旧网关标识） | 5 条｜31,073 tokens |
| 标注计算耗时 | 10:15 轮 1.7 分 + 10:53 轮 6.0 分 ≈ **7.7 分钟** |

302 次请求的互斥构成：成功 165 + 格式被拒 133 + 传输错误 4 = 302。换编码 104 是另一维度，不与之相加。

## 6. 交付物

| 项 | 路径 |
|---|---|
| 固定清单 | `frozen_scope_1312.json`（1,312）｜增补 `scope_supplement.json`（0） |
| qrels | `qrels.tsv`（2,777 行、无重复） |
| 正式标签 | `judgments/q_*.json`（51 题） |
| 语义修正台账 | `semantic_corrections.json`（8 条） |
| 待裁决清单 | `semantic_pending_resolution.json`（82 条）／`semantic_pending_review.json` |
| 风险筛查（已封闭，不再扩类） | `semantic_risk_screen.json` |
| 抽查工作单 | `semantic_spotcheck_worksheet.txt`（20 条） |
| 合并留痕 | `merge_revisions.json`（196）／`merge_conflicts.json` |
| 机械校验 | `verify_report.json` |
| 补漏任务 | `gapfill/_gapfill_supplement.json` + `gapfill/q_0060/batch_08.txt` |
| 备份 | `judgments_backup_20260918T111208`／`_semantic_20260918T113009`／`_prerevert_*` |

## 7. 真实阻塞

1. **标注执行需用户终端**（凭据本进程不可读）——363 条待执行。
2. **82 条语义裁决需要更强证据或人**：§八 的"块内是否引述/归因"无法用字段或词表可靠判定；P4 的事实命中不足不能作为降级依据。
3. 指标在 1 与 2 完成前无法出具。

**标注为 AI 生成，非人工、非领域专家；`semantic_*` 全部结论为 Agent 复核，人工验收未完成。** 本次抽查为定向分层抽样，不代表全库错误率。
