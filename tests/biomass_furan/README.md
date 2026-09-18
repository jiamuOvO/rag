# 生物质与呋喃检索测评

这是 Li_Jia RAG 的冻结检索 benchmark。当前版本包含 60 道问题、48 个来源和 5,066 个文本块；frozen scope 有 1,312 个组合，其中 1,194 个已有等级、118 个保持 `exhausted_unresolved`。结果状态为 `PROVISIONAL_UNJUDGED_AS_ZERO`，不是完整人工金标。

## 先读

- `EVALUATION_GUIDE.md`：当前维护、增量标注、重评分和红线规则。
- `ANNOTATION_INSTRUCTIONS.md`：冻结的四级相关性判定规范。
- `dataset_card.md`：数据集来源、结构和限制。
- `FINAL_DELIVERY_2026-09-18.md`：首版最终交付留痕。
- `../../docs/检索测评结果总结-2026-09-18.md`：面向普通读者的结果解释和术语表。

## 权威数据

- 题目：`queries.jsonl`
- 语料：`corpus.jsonl`、`sources.jsonl`
- 固定范围：`frozen_scope_1312.json`
- 标签唯一来源：`judgments/q_*.json`
- frozen qrels：`qrels_frozen_scope_1312.tsv`
- 状态账：`final_state.json`
- 原始检索结果：`results/<运行ID>/runs.jsonl`
- 当前派生摘要：`results/<运行ID>/summary_provisional.json`

任何 judgments 变更后，都必须在同一轮重建 qrels、状态账和评分摘要。不得把未判定组合自动补成 0，也不得覆盖历史 run。

## 常用命令

```powershell
$P = 'D:\anaconda3\envs\rag_lijia\python.exe'

# 重评已经保存的运行
& $P -B tests\biomass_furan\evaluate.py score `
  tests\biomass_furan\results\<运行ID> `
  --provisional `
  --qrels tests\biomass_furan\qrels_frozen_scope_1312.tsv

# 机械校验
& $P -B tests\biomass_furan\coverage_scope.py
& $P -B tests\biomass_furan\verify_judgments.py
& $P -B tests\biomass_furan\selfcheck_verify.py

# benchmark 回归
& $P -m pytest -q tests\biomass_furan\test_benchmark.py
```

`snapshot/`、`runtime/` 和标注工作目录由 `.gitignore` 排除。冻结 snapshot 与 `runtime/model_responses` 具有复现和审计价值，不应作为普通缓存删除。
