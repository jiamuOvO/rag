# rag
关于生物质和呋喃小分子的rag系统
# Li_Jia 科研论文 RAG（v0.1 baseline）

> 最后更新时间：2026-09-09 11:00（Asia/Shanghai，精确到小时）

## 更新记录

### 2026-09-09 11:00：浏览器验收、结构化抽取与诊断集

- 使用真实浏览器验证管理员登录、中文问答、降级警告、论文列表、34 页 OCR 复核、5 个失败页、chunk 关系、近期任务和查询候选诊断。
- 新增保守的 `reaction-evidence-v1` JSON Schema 抽取；原始值、归一化值、单位和 evidence_id 分开持久化，冲突记录并存。
- 新增默认关闭的 reranker 接口及 `lexical_coverage` 可解释实现，诊断保存重排前后顺序。
- 查询诊断拆分保存规范化问题、基础 tokens、同义词 expansions，并显示候选原文。
- 查询候选保存不可变的论文/页码/章节/原文快照，重切块后历史 request_id 仍可完整复核；回答文本、实际 provider/model 和 prompt 版本同步保存。
- chunk 协议新增稳定 ordinal、overlap_tokens 和 chunk_reason；页面可反查关联 chunks。
- API 不再向匿名论文列表暴露服务器路径/source hash，任务错误和诊断均需管理员登录。
- 新增 6 个真实小样本诊断案例；首次运行发现无关问题被常用词误召回，修复后 6/6 通过。
- 自动化测试现为 21 项，全部通过；覆盖真实浏览器 HTTP 闭环、正常生成、全部主要降级和引用校验路径。

### 2026-09-09 11:10：可审计 Web v0.2 第一阶段

- 新增浏览器界面：论文列表、受控 PDF 上传、任务查询、中英文问答、证据卡片、原 PDF 与 chunk 复核。
- 新增 PBKDF2 管理员密码、HttpOnly 会话；未配置密码时管理操作关闭，不以匿名模式运行。
- 入库升级为 SQLite 持久队列；服务重启时遗留 `running` 会明确变为 `interrupted`，可创建关联重试。
- 新增可重复数据库迁移、逐页产物、任务阶段和查询候选诊断表。
- 查询响应新增标准化查询、警告和语料覆盖状态；模型引用未知证据 ID 时显式降级。
- 完整语料现保存 18 篇论文、258 个物理页面、929 chunks 和 9 个 SI 登记；扫描件 5 个无文本页保留失败记录。
- 新增 Dockerfile/Compose 骨架与 [现状审计](./现状审计-2026-09-09.md)。回归测试为 13 项。

### 2026-09-09：Conda、双模型 RAG 与安全配置

- 创建并验证 Conda 环境 `rag_lijia`，使用 Python 3.11；后续运行默认采用该环境。
- 新增本地 [config.yaml](./config.yaml) 与可复制模板 [config.example.yaml](./config.example.yaml)。普通模型配置写入 YAML，API Key 只从 `RAG_CHAT_API_KEY` 等环境变量读取。
- `config.yaml` 和 `.env` 已加入 `.gitignore`；如果 YAML 中出现明文 `chat.api_key` 或 `embedding.api_key`，程序会拒绝启动。
- 配置 `Qwen3.6-35B-A3B` 作为答案生成模型，负责依据检索证据组织回答和引用。
- 配置 `Qwen3-Embedding-0.6B` 作为向量模型，负责生成论文 chunk 向量和问题向量；两个模型使用不同 model、不同 base URL，但共用同一个 API Key。
- 新增 OpenAI-compatible Embedding 接口、批量请求、返回数量/维度/有限值校验，以及 SQLite float32 向量持久化。
- 检索升级为 BM25 关键词召回与 Dense 余弦相似度召回，再通过 Reciprocal Rank Fusion（RRF）融合排名。
- 新增 embedding 模型名、provider、向量维度和数量检查；切换模型或维度时必须重建向量，禁止混用不同版本。
- `doctor` 新增 `embedding_configured` 和 `embedding_index` 状态；API `/ready` 会在双模型配置不完整时返回未就绪。
- 模型未配置或服务调用失败时保留 BM25 与抽取式证据降级，并通过 `degradation_reason` 明确报告失败位置。
- 修复 SQLite 每个连接都启用外键约束，确保 `ingest --force` 能事务化替换论文、chunks 和旧向量。
- 自动化回归测试更新为 11 项，覆盖 Dense 检索、RRF、YAML/环境变量密钥分离、明文密钥拒绝及强制重建。

当前状态：代码已经支持两个模型，但 `config.yaml` 中的 `base_url` 仍为空，API Key 也未由用户注入，因此现有 929 个 chunks 尚未生成向量，`embedding_index.count` 当前为 `0`。填写配置后需要运行 `python -m rag.cli ingest --force`。

这是一个面向生物质与呋喃论文的小规模、可诊断 RAG 第一版。它不复制旧项目中依赖缺失、异常吞没和不稳定 ID 的实现，而是提供一条可独立运行的基线：

```text
PDF 发现 → 文本提取 ─┬→ 结构感知切块 → Qwen Embedding → SQLite 原子入库
                     └→ 低文本页 → RapidOCR
问题 → BM25 + 向量召回 → RRF 融合 → 证据筛选 → Qwen 35B 生成 → 引用回答
          每个阶段：request_id / run_id / paper_id + JSONL 耗时和错误码
```

当前配置预留 Qwen3-Embedding-0.6B 做语义检索、Qwen3.6-35B-A3B 做答案生成。两者分别配置 model 和 base URL，但通过 `api_key_env` 共用同一个环境变量中的 API Key。未填写有效 base URL/Key 或服务失败时会明确标记降级并使用 BM25/抽取式证据，不会悄悄自由生成。

## 1. 快速开始（Conda + PowerShell）

推荐使用已经创建好的 Conda 环境 `rag_lijia`：

```powershell
Set-Location -LiteralPath 'F:\RAG\Li_Jia'
conda activate rag_lijia
python -m rag.cli doctor
```

如果 PowerShell 还不能识别 `conda activate`，先执行一次 `D:\anaconda3\Scripts\conda.exe init powershell`，关闭并重新打开 PowerShell。也可不激活，直接运行 `D:\anaconda3\Scripts\conda.exe run -n rag_lijia python -m rag.cli doctor`。

需要从零重建时：

```powershell
conda create -n rag_lijia python=3.11 pip -y
conda activate rag_lijia
python -m pip install -r requirements.lock.txt
python -m pip install -e .
```

原有 `.venv` 方式仍可使用，但后续命令和验证以 `rag_lijia` 为准。

入库与问答：

```powershell
python -m rag.cli ingest
python -m rag.cli query "Which conditions produced the highest furfural yield?"
python -m rag.cli query "木糖制糠醛的反应条件是什么？" --top-k 8
python -m rag.cli status
```

只重建一篇或强制重建已有论文：

```powershell
python -m rag.cli ingest --file '.\data\2020_microwave_xylose_to_furfural_PMC7464547.pdf'
python -m rag.cli ingest --force
```

启动 HTTP API（Swagger 位于 `http://127.0.0.1:8000/docs`）：

```powershell
python -m rag.cli hash-password
# 将输出中的 password_hash 通过环境或密钥管理服务设置为 RAG_ADMIN_PASSWORD_HASH；不要写入仓库。
$env:RAG_ADMIN_PASSWORD_HASH = '<PBKDF2 hash>'
$env:RAG_SESSION_SECRET = '<long random secret>'
python -m rag.cli serve --host 127.0.0.1 --port 8000
```

浏览器网站位于 `http://127.0.0.1:8000/`。管理员密码未配置时，公开问答和论文状态仍可查看，但上传、任务错误、页面/chunk 诊断和原 PDF 访问会返回 `AUTH_NOT_CONFIGURED`，不会匿名开放。

Docker Compose 使用相同两个必填秘密环境变量：

```powershell
docker compose up --build
```

`data` 在容器中以只读卷挂载，运行数据库、上传和日志保存在独立 `rag-var` 卷。若通过 HTTPS 反向代理公开服务，将 `RAG_COOKIE_SECURE=1`。本机本轮 Compose 配置校验通过；由于 Docker Desktop 引擎未启动，镜像实际构建仍需在引擎可用后复验。

## 2. CLI 与 API

CLI：

| 命令 | 作用 |
| --- | --- |
| `doctor` | 检查 Python、PyMuPDF、RapidOCR、数据目录、SQLite 和生成模型配置 |
| `ingest [--force] [--file PATH]` | 同步入库全部或指定 PDF；返回可追踪的 `run_id` |
| `query QUESTION [--top-k 8] [--paper-id ID]` | 检索、回答并返回稳定证据；`--paper-id` 可重复 |
| `status [--run-id ID]` | 查看近期运行或某次运行的逐论文状态 |
| `serve` | 启动 FastAPI 服务 |
| `hash-password` | 交互式生成 PBKDF2 管理员密码哈希，不把密码写入命令历史 |
| `evaluate [--cases PATH]` | 运行轻量诊断问题集并返回每例 request_id 和失败环节提示 |

HTTP：

| 方法与路径 | 行为 |
| --- | --- |
| `GET /health` | 进程和 SQLite 存活检查 |
| `GET /ready` | PDF/OCR/模型依赖及数据目录就绪状态 |
| `POST /v1/uploads` | 管理员以 `application/pdf` 请求体和 `X-Filename` 上传，创建持久任务 |
| `POST /v1/ingestions` | 管理员创建全库 SQLite 持久任务，body 为 `{"force": false}` |
| `GET /v1/runs` / `GET /v1/runs/{run_id}` | 管理员查看近期任务或某次运行的阶段、计数和结构化错误 |
| `POST /v1/runs/{run_id}/retry` | 为 failed/partial_failed/interrupted 任务创建有关联的新运行 |
| `POST /v1/query` | body 为 `{"question":"...","top_k":8,"paper_ids":null}` |
| `GET /v1/papers` | 论文状态、解析计数与 SI 附件登记 |
| `GET /v1/papers/{paper_id}/pages` | 管理员查看逐页文本、OCR 置信度、错误和关联 chunk IDs |
| `GET /v1/papers/{paper_id}/chunks` | 管理员分页查看 chunk 来源、相邻块、overlap 和版本 |
| `GET /v1/papers/{paper_id}/pdf` | 管理员受控访问允许目录内的原始 PDF |
| `GET /v1/queries/{request_id}` | 管理员查看分词、扩展、BM25/Dense/RRF/重排候选和最终证据 |
| `GET /v1/extractions/schema` / `GET /v1/extractions/{id}` | 管理员查看抽取 JSON Schema 和落库结果 |

问答响应中的 `evidence` 包含 `evidence_id`、论文文件名、`paper_id`、页码、章节、chunk ID、原文片段和排序分数。正常配置后该分数是 BM25 与向量排名的 RRF 融合分数。`answer_mode=extractive_demo` 表示生成模型未调用成功；`degraded=true` 会同时给出降级错误码。

## 3. 配置与密钥保护

程序会自动读取项目根目录的 `config.yaml`。可提交的模板是 `config.example.yaml`；本地 `config.yaml` 已加入 `.gitignore`。

常见安全做法是“普通配置与秘密分离”：模型地址、模型名和超时放 YAML，API Key 由环境变量或生产密钥管理服务注入。不要把 Key 写进 YAML、源码、README、命令历史、日志或提交记录。本项目若检测到 `chat.api_key` 出现在 YAML，会直接拒绝启动。

`config.yaml` 的模型部分：

```yaml
chat:
  provider: openai_compatible
  base_url: "REPLACE_WITH_CHAT_BASE_URL"
  model: "Qwen3.6-35B-A3B"
  timeout_seconds: 60
  api_key_env: RAG_CHAT_API_KEY

embedding:
  provider: openai_compatible
  # 与 chat 使用不同的服务地址
  base_url: "REPLACE_WITH_EMBEDDING_BASE_URL"
  model: "Qwen3-Embedding-0.6B"
  timeout_seconds: 60
  batch_size: 10
  api_key_env: RAG_CHAT_API_KEY
```

启动程序前，在当前 PowerShell 会话注入 Key：

```powershell
$env:RAG_CHAT_API_KEY = '你的密钥'
conda activate rag_lijia
python -m rag.cli doctor
python -m rag.cli serve
```

关闭该 PowerShell 窗口后，这个进程级环境变量随之消失。共享或生产环境应改用操作系统凭据库或云端 Secret Manager，并定期轮换 Key、限制权限和用量。若 Key 曾经进入 Git，应立即撤销并换新，仅从文件中删除是不够的。

环境变量仍可覆盖 YAML，适合部署平台注入：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `RAG_DATA_DIR` | 项目下 `data` | 原始 PDF/SI，只读使用 |
| `RAG_VAR_DIR` | 项目下 `var` | SQLite 与日志目录 |
| `RAG_OCR_ENABLED` | `1` | 文本不足页面启用 OCR |
| `RAG_MIN_PAGE_CHARS` | `80` | 低于该字符数触发 OCR |
| `RAG_CHUNK_TOKENS` | `384` | baseline 目标切块长度 |
| `RAG_CHUNK_OVERLAP` | `64` | baseline 重叠长度 |
| `RAG_CHAT_PROVIDER` | `extractive` | 可设为 `openai_compatible` |
| `RAG_CHAT_BASE_URL` | 空 | OpenAI-compatible `/v1` 根地址 |
| `RAG_CHAT_API_KEY` | 空 | 名称可由 `chat.api_key_env` 改写；只从环境读取，不写日志 |
| `RAG_CHAT_MODEL` | 空 | 生成模型名 |
| `RAG_CHAT_TIMEOUT_SECONDS` | `60` | 模型请求超时 |
| `RAG_EMBEDDING_PROVIDER` | YAML 配置 | 可设为 `openai_compatible` 或 `disabled` |
| `RAG_EMBEDDING_BASE_URL` | YAML 配置 | 独立的 Embedding 服务 `/v1` 根地址 |
| `RAG_EMBEDDING_MODEL` | YAML 配置 | Embedding 模型 API ID |
| `RAG_EMBEDDING_BATCH_SIZE` | `10` | 每次 embedding 请求的 chunk 数 |
| `RAG_DEBUG` | `0` | 增加错误 traceback 长度；仍不记录正文和密钥 |
| `RAG_ADMIN_PASSWORD_HASH` | 空 | `hash-password` 生成；为空时管理操作关闭 |
| `RAG_SESSION_SECRET` | 空 | 部署环境注入的长随机会话签名密钥 |
| `RAG_COOKIE_SECURE` | `0` | TLS 反向代理生产环境设为 `1` |
| `RAG_RERANKER_PROVIDER` | `disabled` | 可设为 `lexical_coverage`，默认关闭 |
| `RAG_STRUCTURED_EXTRACTION_ENABLED` | `0` | 开启 evidence-bound 结构化抽取原型 |

OpenAI-compatible 示例：

```powershell
$env:RAG_CHAT_PROVIDER = 'openai_compatible'
$env:RAG_CHAT_BASE_URL = 'https://your-service.example/v1'
$env:RAG_CHAT_API_KEY = 'your-secret'
$env:RAG_CHAT_MODEL = 'your-model'
python -m rag.cli query "Summarize the reported furfural yields."
```

业务代码只依赖 `ChatProvider` 接口。服务不可用、返回格式错误或配置不完整时，响应标记降级并返回检索证据，不伪造正常生成结果。

填好 URL 和 Key 后必须重建一次索引，给现有 929 个 chunks 生成向量：

```powershell
python -m rag.cli doctor
python -m rag.cli ingest --force
```

`doctor` 中 `embedding_configured` 应为 `true`；入库结束后 `embedding_index.count` 应等于 chunk 数。切换 embedding 模型或维度后也必须执行 `--force`，不同模型的向量不会混用。

注意：`--force` 会重新解析 PDF 和 OCR，并产生约 93 个 embedding 批请求（当前 929 chunks、批大小 10）。请先确认服务配额和费用。后续应增加“仅补向量、不重新解析 PDF”的独立维护命令。

## 4. 如何快速定位错误

运行状态保存在 `var\rag.sqlite3`，短结构化日志保存在 `var\logs\rag.jsonl`。入库输出会直接给出 `run_id`；随后运行：

```powershell
python -m rag.cli status --run-id run_xxx
Get-Content .\var\logs\rag.jsonl | Select-String 'run_xxx'
```

固定阶段为 `discover / parse / ocr / chunk / index / retrieve / generate`。错误对象包含：

```json
{
  "error_code": "OCR_PAGE_FAILED",
  "stage": "ocr",
  "message": "...",
  "exception_type": "...",
  "retryable": false,
  "traceback": "..."
}
```

默认日志只写阶段开始/结束、毫秒耗时、输入输出数量和安全截断后的异常，不写论文全文、prompt、Authorization 或 API Key。它避免逐 token/逐块 trace，因而对主链路影响很小。单篇论文失败会写入 `run_documents` 并继续下一篇；最终状态是 `completed`、`partial_failed` 或 `failed`。论文及 chunks 在同一 SQLite 事务中替换，零 chunk 无法被标记为 `ready`。

## 5. 数据和目录

```text
Li_Jia/
├─ data/                 原始正文 PDF、SI ZIP、论文清单（不修改）
├─ src/rag/              解析、切块、存储、检索、生成、CLI/API
├─ tests/                单元与端到端测试
├─ scripts/              安装及 OCR smoke test
├─ var/                  运行生成：SQLite 和 JSONL（不提交）
├─ requirements.lock.txt 固定的第一版依赖
└─ 旧项目RAG技术复用评估.md
```

`paper_id` 来源于文件内容 SHA-256；重复内容即使改名也不会重复入库。SI ZIP 根据去掉 `_SI` 后的文件名关联正文，登记为 `registered_not_parsed`，不解压也不参与检索。

## 6. 验证

```powershell
python -m pytest -q
python .\scripts\smoke_ocr.py
```

第一条覆盖稳定 ID、化学/数值 token、中英文 token、切块链接与 overlap、BM25、证据格式、脱敏、合成 PDF 端到端、幂等、论文范围约束和 API 校验。第二条只 OCR `1922_Commercial_Furfural_scanned.pdf` 前两页，验证扫描件路径而不耗时处理整本。

真实数据验收使用 `ingest` 返回的 `counts` 核对 discovered、succeeded、failed、pages、ocr_pages、chunks 和 attachments。

本机 2026-09-08 的整批 smoke test：发现 18 篇 PDF；此前已入库的 1 篇被幂等跳过，其余 17 篇成功、0 篇失败；本轮处理 234 个有效页面、34 个 OCR 页面、生成 886 个新 chunks，并登记 9 个 SI。1922 扫描件有 5 个页面未识别出文字，但论文仍有 29 个有效页面并成功入库；这些页面保留 `OCR_NO_TEXT` 状态，没有伪装成正常页面。总耗时约 268 秒，主要消耗在历史扫描件 OCR。

在完整语料的 20 次查询微基准中，未缓存版本平均约 236 ms/次；开启与关闭阶段日志分别约 233 ms 与 236 ms，差异落在运行波动内，未观察到明显日志开销。HTTP 常驻进程缓存全库 BM25 索引，入库后自动失效重建；缓存后的实测查询约 3.4 ms。CLI 每次启动需从 SQLite 恢复一次。

## 7. 当前能力边界

### 已实现并可验证

- 普通 PDF 逐页文本提取、低文本页 RapidOCR 降级和 OCR 置信度记录。
- 内容哈希稳定 ID、结构/页面优先切块、相邻块链接和 overlap。
- SQLite WAL、论文与 chunks 事务化替换、重复入库幂等。
- 英文、化学式、数值/单位 token 与中文字符 bigram 的 BM25 baseline。
- OpenAI-compatible Embedding 批量调用、返回数量/维度/有限值校验及 float32 向量持久化。
- 查询向量余弦召回，以及 BM25 与 Dense 排名的 RRF 融合。
- 用户指定论文范围、无证据拒答、稳定 evidence ID、页码原文引用。
- CLI、HTTP API、运行状态、逐论文失败隔离、结构化错误和轻量耗时日志。

### 只是跑通链路的 Demo，需要优化

- `extractive_demo` 只拼接最相关原文，不是完整的分析或总结模型。
- 标题识别和结构切块使用启发式规则，对双栏阅读顺序、跨页段落及复杂版式不保证正确。
- 中文问英文依赖共享术语、化学名和数值，尚不是跨语言语义检索。
- BM25、Dense 与 RRF 参数尚未用标注问题集校准。
- HTTP 入库任务使用进程内线程；进程重启时不会恢复正在运行的任务。
- 整批入库当前按文件名串行处理；扫描件排在前面时会推迟现代文本 PDF，应在后续改成 OCR 独立队列和受控并行。
- OCR 是通用英文模型，历史字体、表格、上下标及化学式可能识别错误。
- 引用表明使用了哪个检索文本块，不等于逐声明的科学蕴含验证。

### 仍然缺失

- Reranker、经过评测的检索阈值和领域检索评测集。
- 表格单元格、图片/图注、公式、bbox、对象级证据和 SI 内容解析。
- 生物质/底物/催化剂/产物领域 Schema、同义词、实体及单位归一。
- 结构化反应记录抽取、JSON Schema 校验、冲突检测和人工复核队列。
- 逐声明引用验证、citation precision/recall、固定问答评测集与回归指标。
- 持久任务队列、鉴权、限流、多进程协调、前端、容器及生产监控。

这些边界与 `旧项目RAG技术复用评估.md` 的结论一致：旧项目提供了方向参考，但其硬编码密钥、吞异常、文件名 ID、未验证阈值和缺失模块没有进入本实现。
