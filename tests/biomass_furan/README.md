# 生物质与呋喃检索测评

**当前按用户要求暂停，禁止自动续跑。** 已保存351/303,960对判断，完整语义标注和复核尚未完成。恢复前先阅读 [安全暂停交接](HANDOFF_2026-09-16.md) 与 `stop_state.json`，尤其是调用硬预算、超时重试及尚未验证的思考模式修正。

先看 `validation_report.json`。60题候选集已经形成，但全量标注、AI复核与人工验收是独立状态；未完成qrels时不能发布正式Recall/nDCG等分数。

在项目目录的PowerShell中，按原有方式启动服务并启用本地测评桥：

```powershell
.\scripts\start.ps1 -Benchmark
```

该开关仅允许loopback监听，生成一次性本地授权令牌；模型密钥仍由原启动脚本隐藏读取。普通启动没有测评接口。接口只接收冻结数据集的文档/问题ID，不接受任意提示词或文件路径。运行结束自动移除令牌。

在另一终端执行（可由代理执行，无需再次输入密钥）：

```powershell
python -B tests/biomass_furan/annotate.py run --bridge http://127.0.0.1:8000 --max-batches 1
python -B tests/biomass_furan/annotate.py run --bridge http://127.0.0.1:8000
python -B tests/biomass_furan/annotate.py review --bridge http://127.0.0.1:8000
python -B tests/biomass_furan/annotate.py export
python -B tests/biomass_furan/validate.py
python -B tests/biomass_furan/evaluate.py http
```

`run`自动跳过已完成批次；中断不会把未判断项标为0。出现分歧时必须裁决后再验收，不自动覆盖种子证据。

隔离模式及离线对照：

```powershell
python -B tests/biomass_furan/secure_run.py
python -B tests/biomass_furan/evaluate.py isolated --offline-bm25
python -B -m pytest tests/biomass_furan/test_benchmark.py -q -p no:cacheprovider
```

所有评测输出写在本目录；生产HTTP检索会按服务正常行为写入查询日志，这是用户后续明确授权的例外。为复用服务凭据，仅在 `scripts/start.ps1` 增加可选开关、在 `src/rag/api.py` 增加默认关闭的桥接加载入口；检索算法与原始知识库不变。

真实结果位于 `results/<运行ID>/`。首轮生产服务中途停止的结果保留，不能删除失败后声称稳定。模型关闭的BM25对照不冒充正常混合检索。`score`默认需要完整qrels；`--provisional`只输出明确标记的未判断按0诊断文件，不属于正式报告。

要按新标注重新计算已保存运行，执行：

```powershell
python -B tests/biomass_furan/evaluate.py score tests/biomass_furan/results/<运行ID>
```

`snapshot/`、`runtime/`和缓存被本目录.gitignore排除；六个核心交付文件、标注状态和测试脚本保留。不要公开发布临时令牌或原始数据库快照。
