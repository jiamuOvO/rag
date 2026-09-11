# Li_Jia 科研论文 RAG（v0.3 多作用域模块）

## 当前状态快照（2026-09-11）

- 真实 SQLite 已迁移到 schema v14，`integrity_check=ok`、WAL 开启、外键检查无违规。
- 官方物理语料保持 18 篇论文、258 页、929 chunks 和 929 embeddings；18 个官方逻辑文档全部归入默认官方库。
- 官方库、用户私人库和会话临时资料已在数据、权限、SQL 召回、PDF 和 evidence 回查层实现隔离。
- 自动化回归为 36/36，OpenAPI 3.1 可生成（35 条路径），真实语料评测为 6/6。
- 真实浏览器已验证会话恢复、私人/临时 PDF 上传、TTL、提升、多作用域问答、证据面板、管理员三页和窄屏抽屉。
- 当前默认可以在模型不可用时显式降级到 BM25/抽取式证据；真实外部 Chat/Embedding 生产链路仍需部署方提供有效服务地址和密钥后验证。

## v0.3 多作用域架构（2026-09-10）

本项目是可嵌入上层业务的科研 RAG 子模块，不承担完整用户中心。核心边界为：

- `Principal(tenant_id, subject, roles, auth_method)` 由 FastAPI 统一依赖解析；生产默认不信任 `X-User-ID`。现有管理员 HttpOnly 会话继续兼容。开发身份仅在 `RAG_DEV_AUTH_ENABLED=1` 且 `RAG_ENV!=production` 时接受 `X-RAG-Dev-Subject`。
- `collections` 区分 `official`、`private`、`temporary`；现有 18 篇论文由幂等迁移自动映射到 `col_official_default`，旧 `paper_id/chunk_id` 保持不变。
- `collection_documents` 是作用域内逻辑文档。相同内容可以安全映射到多个作用域，共享稳定的物理论文解析结果；列表、检索、证据和 PDF 均从逻辑文档做服务端授权。删除最后一个逻辑引用时才级联删除 pages、chunks 和 embeddings。迁移 v13/v14 同时把租户、会话、选中 collection 和逻辑来源固化进查询候选快照。
- 私人和临时检索通过 SQL join 在候选构建前过滤，不能先搜全库再由前端隐藏。临时资料还必须匹配当前 conversation 且未超过 `expires_at`。
- `conversations/messages` 保存标题、用户问题、独立检索问题字段、回答、引用和时间；每轮引用仍绑定本轮候选 evidence ID。
- 临时文件默认 24 小时 TTL。后台工作线程会从持久数据库重复扫描，管理员 API 和 `cleanup-temp` CLI 可手动触发；文件删除只允许在 `var/private` 与 `var/temporary` 管理根目录下进行。

### 新增 HTTP API

| 方法与路径 | 说明 |
| --- | --- |
| `GET/POST /v1/conversations` | 列出或创建自己的会话 |
| `GET/PATCH/DELETE /v1/conversations/{id}` | 恢复、重命名或删除自己的会话 |
| `GET/POST /v1/collections` | 列出可访问知识库或创建私人研究库 |
| `PATCH/DELETE /v1/collections/{id}` | 重命名或删除自己的私人库；官方库只读 |
| `GET/POST /v1/collections/{id}/documents` | 搜索/列出或异步上传私人 PDF |
| `GET/POST /v1/conversations/{id}/documents` | 列出或异步上传本会话临时 PDF |
| `DELETE /v1/documents/{id}` | 删除自己的私人/临时逻辑文档及受控文件 |
| `GET /v1/documents/{id}/status` | 查看自己的异步处理阶段和安全错误摘要 |
| `POST /v1/documents/{id}/promote` | 将临时文档保存到指定私人库 |
| `GET /v1/documents/{id}/pdf` | 经作用域授权读取 PDF |
| `POST /v1/retrieve` | 仅执行 BM25 + Dense + RRF + 可选 reranker，不调用生成模型 |
| `GET /v1/evidence/{evidence_id}` | 经作用域授权返回来源、论文、页码、章节和原文 |
| `POST /v1/admin/temporary-documents/cleanup` | 管理员幂等清理过期临时资料 |
| `GET /v1/queries` | 管理员查看近期查询状态、错误阶段与耗时 |

`POST /v1/query` 保持旧 `question/top_k/paper_ids` 字段，并增加：

```json
{
  "question": "这些条件下产率如何？",
  "conversation_id": "conv_...",
  "collection_ids": ["col_..."],
  "include_official": true,
  "top_k": 8
}
```

未带私人范围的旧请求保持官方库兼容。指定私人库或 conversation 时必须具有已验证身份。响应证据增加 `document_id`、`collection_id` 和 `source_type`（`official/private/temporary`）。

### 上层系统接入条件

生产接入方需要提供可信身份适配器，将 JWT、可信反向代理身份或内部服务凭据转换为 `Principal`，并明确 tenant、subject 与 roles。可以向 `app.state.principal_resolver` 注入上层 JWT 解析器，或配置 `RAG_TRUSTED_IDENTITY_SECRET` 使用带 60 秒时效 HMAC 签名的 `X-RAG-Tenant/Subject/Roles/Identity-Timestamp/Identity-Signature` 可信代理协议。部署必须提供 `RAG_SESSION_SECRET`、管理员密码哈希以及模型密钥环境变量；不得开启开发身份头。当前实现不绑定特定厂商 JWT/JWKS，以免把本模块变成另一个用户中心。

> 最后更新时间：2026-09-11 10:00（Asia/Shanghai，精确到小时）

## 更新记录

### 2026-09-11 10:00：v0.3 多作用域真实浏览器验收

- 实测新建、恢复和重命名会话，创建和重命名私人研究库，私人/临时 PDF 上传到 `ready`，临时资料到期时间和提升，三作用域问答与旁边证据面板。
- 实测管理员文档处理、retrieve-only 排名和运行日志；390×844 视口下证据面板正确变为抽屉。
- 修复共享物理论文的 evidence 回查丢失逻辑来源、临时上传值残留，以及内置浏览器不支持原生 `prompt/confirm` 的交互问题。

### 2026-09-09 15:00：备份与容器端到端验收

- 新增非覆盖式 SQLite 在线 `backup` 命令；真实库快照通过完整性检查并保留 18 篇论文、258 页、929 chunks 和迁移 v10。
- Docker Compose 镜像完成实际构建；首次 smoke 暴露 OpenCV GUI 动态库问题，改用固定版本 headless OpenCV，并增加有限 pip 重试、超时和 BuildKit 缓存。
- 修复后容器达到 healthy 且 `/ready=true`；RapidOCR 可导入，首页返回 200。
- 在只读 `data` 与独立 volume 上完成容器单篇入库（18 页、38 chunks）及 HTTP 严格范围问答，首条证据定位第 7 页并包含 130°C / 71.1%。
- 自动化回归保持 25 项全通过，并新增对 embedding、reranker、引用绑定和 OCR 降级日志的直接断言。

### 2026-09-09 14:00：真实语料恢复与最终回归

- 完整语料安全重建发现并修复 SI 外键替换顺序问题；失败事务均已回滚，随后逐篇重试成功，原始 PDF、SI 与本地秘密配置未修改。
- 当前真实库为 18 篇论文、258 个物理页面、929 chunks 和 9 个 SI 登记；34 个 OCR 页面中 5 页明确保存为 `OCR_NO_TEXT`。
- 项目 `.venv` 中自动化回归全部通过；其中直接覆盖管理员重试权限、`parent_run_id` 血缘及引用回查入口，轻量真实语料评测为 6/6。
- 真实浏览器进一步验证重复 PDF 上传去重，以及从回答证据一键跳到正确论文并展开对应页面 chunk 的回查闭环。

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
- 配置 `Qwen3-Embedding-0.6B` 作为向量模型，负责生成论文 chunk 向量和问题向量；两个模型使用不同 model、base URL 和 API Key。
- 新增 OpenAI-compatible Embedding 接口、批量请求、返回数量/维度/有限值校验，以及 SQLite float32 向量持久化。
- 检索升级为 BM25 关键词召回与 Dense 余弦相似度召回，再通过 Reciprocal Rank Fusion（RRF）融合排名。
- 新增 embedding 模型名、provider、向量维度和数量检查；切换模型或维度时必须重建向量，禁止混用不同版本。
- `doctor` 新增 `embedding_configured` 和 `embedding_index` 状态；API `/ready` 会在双模型配置不完整时返回未就绪。
- 模型未配置或服务调用失败时保留 BM25 与抽取式证据降级，并通过 `degradation_reason` 明确报告失败位置。
- 修复 SQLite 每个连接都启用外键约束，确保 `ingest --force` 能事务化替换论文、chunks 和旧向量。
- 自动化回归测试更新为 11 项，覆盖 Dense 检索、RRF、YAML/环境变量密钥分离、明文密钥拒绝及强制重建。

当前状态：代码已支持独立 Chat 和 Embedding provider。真实库现有 929 个 chunks 和 929 条同维度 embedding；切换 embedding provider、model 或维度时仍必须先执行非覆盖备份，再用 `python -m rag.cli embed` 原子重建，不需重新解析 PDF 或 OCR。本次最终浏览器验收使用抽取式 provider，不将其当作外部生成模型的正常路径验证。

这是一个面向生物质与呋喃论文的小规模、可诊断 RAG 第一版。它不复制旧项目中依赖缺失、异常吞没和不稳定 ID 的实现，而是提供一条可独立运行的基线：

```text
PDF 发现 → 文本提取 ─┬→ 结构感知切块 → Qwen Embedding → SQLite 原子入库
                     └→ 低文本页 → RapidOCR
问题 → BM25 + 向量召回 → RRF 融合 → 证据筛选 → Qwen 35B 生成 → 引用回答
          每个阶段：request_id / run_id / paper_id + JSONL 耗时和错误码
```

当前配置预留 Qwen3-Embedding-0.6B 做语义检索、Qwen3.6-35B-A3B 做答案生成。两者分别配置 model、base URL 和 `api_key_env`，默认使用独立的 API Key 环境变量。未填写有效 base URL/Key 或服务失败时会明确标记降级并使用 BM25/抽取式证据，不会悄悄自由生成。

## 1. 快速开始（Conda + PowerShell）

推荐使用已经创建好的 Conda 环境 `rag_lijia`：

### 一键安全启动

`scripts/start.ps1` 会分别隐藏读取 Chat 与 Embedding API Key，仅保存到当前进程环境中，不写入配置、磁盘或命令历史；同时自动生成临时会话签名密钥。启动前会分别发送一个最小请求检查 Chat `/chat/completions` 与 Embedding `/embeddings`，只有两个接口都可用才启动网站：

```powershell
conda activate rag_lijia
Set-Location -LiteralPath 'F:\RAG\Li_Jia'
.\scripts\start.ps1
```

模型地址与模型 ID 仍放在本地 `config.yaml`，API Key 不得写入 YAML。诊断输出只显示 Key 是否存在，不显示 Key 内容。需要使用其他配置文件时可传入 `-ConfigPath`。

更新记录（2026-09-10 10 时，Asia/Shanghai）：新增 Chat/Embedding 两个独立 API Key 的隐藏输入、临时会话密钥、双接口启动前检查及一键启动脚本。

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

`data` 在容器中以只读卷挂载，运行数据库、上传和日志保存在独立 `rag-var` 卷。若通过 HTTPS 反向代理公开服务，将 `RAG_COOKIE_SECURE=1`。本机 Compose 配置、镜像构建和容器 smoke 均已通过。容器使用 headless OpenCV，避免为 OCR 引入 Mesa/X11 GUI 运行库；pip 设置有限重试、120 秒读取超时和 BuildKit 缓存，以适应较慢网络。

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
| `embed` | 不重新解析/OCR，原子重建全部向量；失败时保留旧索引 |
| `backup [--output PATH]` | 使用 SQLite 在线备份创建一致快照；默认写入 `var/backups`，拒绝覆盖已有文件 |

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
  api_key_env: RAG_EMBEDDING_API_KEY
```

启动程序前，在当前 PowerShell 会话注入 Key：

```powershell
$env:RAG_CHAT_API_KEY = '你的密钥'
$env:RAG_EMBEDDING_API_KEY = '你的 Embedding 密钥'
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
| `RAG_EMBEDDING_API_KEY` | 空 | 名称可由 `embedding.api_key_env` 改写；只从环境读取，不写日志 |
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
$env:RAG_EMBEDDING_API_KEY = 'your-embedding-secret'
$env:RAG_CHAT_MODEL = 'your-model'
python -m rag.cli query "Summarize the reported furfural yields."
```

业务代码只依赖 `ChatProvider` 接口。服务不可用、返回格式错误或配置不完整时，响应标记降级并返回检索证据，不伪造正常生成结果。

填好 URL 和 Key 后必须重建一次索引，给现有 929 个 chunks 生成向量：

```powershell
python -m rag.cli doctor
python -m rag.cli backup
python -m rag.cli embed
```

`doctor` 中 `embedding_configured` 应为 `true`；`embed` 完成后 `embedding_index.count` 应等于 chunk 数。切换 embedding 模型或维度后也应执行 `embed`，不同模型的向量不会混用。

注意：当前 929 chunks、批大小 10 时，`embed` 会产生约 93 个 embedding 批请求。请先确认服务配额和费用。向量先写入临时表，全部成功后再原子替换；调用失败不会破坏现有索引。

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
│  └─ web/               同源浏览器应用（问答、论文、任务、诊断）
├─ tests/                单元与端到端测试
├─ scripts/              安装及 OCR smoke test
├─ var/                  运行生成：SQLite 和 JSONL（不提交）
├─ requirements.lock.txt 固定的第一版依赖
├─ Dockerfile / compose.yaml 轻量单服务部署
├─ 现状审计-2026-09-09.md  当前实现、数据协议与限制审计
└─ 旧项目RAG技术复用评估.md
```

`paper_id` 来源于文件内容 SHA-256；重复内容即使改名也不会重复入库。SI ZIP 根据去掉 `_SI` 后的文件名关联正文，登记为 `registered_not_parsed`，不解压也不参与检索。

## 6. 验证

```powershell
python -m pytest -q
python .\scripts\smoke_ocr.py
python -m rag.cli evaluate
```

测试现为 25 项，覆盖稳定 ID、切块来源、BM25/Dense/RRF/reranker、无证据拒答、引用绑定与一键回查、页面与查询快照、持久任务/中断、管理员认证、上传校验、重试血缘、一致性备份、正常生成及所有主要降级路径。OCR smoke 只处理历史扫描件前两页。`evaluate` 运行 6 个真实语料案例并返回每例 request_id。

真实数据验收使用 `ingest` 返回的 `counts` 核对 discovered、succeeded、failed、pages、ocr_pages、chunks 和 attachments。

本机 2026-09-09 完整重建：18 篇论文、258 个物理页面、929 chunks、9 个 SI 登记。1922 扫描件 34 页全部触发 OCR，29 页有文本，5 页保存为 `OCR_NO_TEXT`，论文和全库运行均标为 `partial_failed`。重建曾暴露 SI 外键替换顺序问题；失败事务全部回滚，修复后 9 篇逐篇重试成功，原始 PDF/SI 未修改。

真实浏览器已验证登录、重复上传去重、中文问答、引用一键回查、论文列表、OCR 页面/错误/置信度、chunk 关系、任务列表和 request_id 候选诊断。轻量问题集为 6/6；其中无关量子问题明确拒答。Docker Compose 镜像已实际构建；容器内 `/ready=true`、RapidOCR 可导入，单篇论文入库得到 18 页/38 chunks，HTTP 查询严格命中该论文第 7 页并返回含 71.1% 的 evidence。

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
- 同源 Web 应用、PBKDF2 管理员认证、HttpOnly 会话、受控 PDF 访问和严格上传校验。
- SQLite 持久任务、重复任务规则、重启中断标记、关联重试和逐阶段组件版本。
- 页面、chunk、不可变查询候选、最终回答、provider/model/prompt 版本和抽取记录的复核 API。
- 默认关闭的可解释 reranker 与 `reaction-evidence-v1` 结构化抽取原型。
- Dockerfile/Compose，其中原始 `data` 只读挂载、运行数据使用独立 volume。

### 只是跑通链路的 Demo，需要优化

- `extractive_demo` 只拼接最相关原文，不是完整的分析或总结模型。
- 标题识别和结构切块使用启发式规则，对双栏阅读顺序、跨页段落及复杂版式不保证正确。
- 中文问英文依赖共享术语、化学名和数值，尚不是跨语言语义检索。
- BM25、Dense 与 RRF 参数尚未用标注问题集校准。
- 后台执行器是单进程、单 worker 的 SQLite 队列；重启会真实标记中断并允许重试，但不支持多个 Uvicorn worker 并发抢占。
- 整批入库按文件名串行处理；当前数据量可接受，扫描件会拉长总耗时。
- OCR 是通用英文模型，历史字体、表格、上下标及化学式可能识别错误。
- 引用表明使用了哪个检索文本块，不等于逐声明的科学蕴含验证。

### 仍然缺失

- 经人工标注扩充的检索阈值与 citation precision/recall 评测；现有 6 例只用于回归和故障归因。
- 表格单元格、图片/图注、公式、bbox、对象级证据和 SI 内容解析。
- 更完整的催化剂/溶剂词典、单位换算、跨 evidence 实验条件组装和人工复核队列；当前 Schema 原型保守且可关闭。
- 逐声明自然语言蕴含验证；当前只验证模型引用 ID 必须来自本次上下文。
- 多进程协调、限流、外部指标系统和 TLS 反向代理配置。
- 真实 chat/embedding 服务的正常路径验证（代码路径已有确定性集成测试；未提供外部模型 URL/Key 时按设计显式降级）。

### 后续更新方向

1. **短期：生产链路硬化。** 接入真实 Chat/Embedding 服务做正常路径、超时、限流和费用验收；配置可靠的 JWT/JWKS 或可信反向代理身份适配器；补充 TLS、Secret Manager、备份恢复演练与运维告警。
2. **中期：提升检索可测性。** 扩展经人工标注的问题集，计算 retrieval/citation precision、recall 和拒答质量，再校准 BM25、Dense、RRF、reranker 及无证据阈值。
3. **中长期：增强科研证据粒度。** 按真实业务优先级增加表格单元格、图注、公式、bbox、SI 和声明级蕴含验证，不在没有标注数据时冒进构建 GraphRAG 或复杂 Agent 平台。
4. **规模化后：拆分执行层。** 当单进程 SQLite 队列成为瓶颈时，再引入可租约的多 worker 任务队列、外部指标系统和容量规划。

这些边界与 `旧项目RAG技术复用评估.md` 的结论一致：旧项目提供了方向参考，但其硬编码密钥、吞异常、文件名 ID、未验证阈值和缺失模块没有进入本实现。

## 8. 依赖、资源与离线影响

本轮没有新增第三方 Python 依赖，继续使用锁定版本。FastAPI/Pydantic 为 MIT，Uvicorn 为 BSD-3-Clause，NumPy 为 BSD，PyYAML 为 MIT，RapidOCR 代码为 Apache-2.0、ONNX Runtime 为 MIT。PyMuPDF 采用 AGPL/商业双许可证，若网站以不符合 AGPL 的方式对外分发或提供修改版，应在部署前由交付方确认商业许可证或完整 AGPL 合规方案。

当前规模下 SQLite、BM25 和向量矩阵均驻留单机即可。常规文本 PDF 主要消耗 CPU 与磁盘；历史扫描件 OCR 是峰值 CPU/内存与耗时来源。929 个 float32 向量的磁盘量取决于模型维度，远小于引入独立向量数据库的固定成本。安装阶段需要 PyPI 或预先准备的 wheel 缓存；运行时 PyMuPDF、RapidOCR、BM25 和 `lexical_coverage` 可离线工作，OpenAI-compatible chat/embedding 需要配置的模型服务可达。模型不可达时系统显式降级，不会伪装正常回答。
