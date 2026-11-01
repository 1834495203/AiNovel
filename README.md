# 纸间 · AI Novel MVP

一个可运行的小说创作工作台：通过对话确认小说设定，在聊天中生成场景，阅读正文、采访不同阶段的角色，并以可回溯的版本管理修改。

Python 3.12+；FastAPI + SQLAlchemy 2 + Pydantic 2 + LangGraph + PostgreSQL/pgvector + Redis；前端使用 Vue 3、TypeScript 和 Vite。Vite 构建产物随 Python 包提供，由 FastAPI 统一服务，运行时不需要单独启动 Node。默认本地单用户使用。

## 启动

已有 PostgreSQL、Redis 可以直接使用。仓库的 docker-compose.yml 保留了你的数据库配置。

```powershell
# 如果数据库容器尚未运行
# 首次启动先复制配置，并将 POSTGRES_PASSWORD 与 DATABASE_URL 中的密码改为同一个自选值。
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
docker compose up -d

# 本次已在 .venv 安装完成；重新安装时仍使用清华镜像
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt -i https://pypi.tuna.tsinghua.edu.cn/simple/
.\.venv\Scripts\python.exe -m pip install -e . --no-deps -i https://pypi.tuna.tsinghua.edu.cn/simple/

# 首次使用且没有 .env 时才复制；已有配置保留
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\ainovel.exe init-db
```

两个终端分别运行：

```powershell
.\.venv\Scripts\ainovel.exe api
```

```powershell
.\.venv\Scripts\ainovel.exe worker
```

打开 [工作台](http://127.0.0.1:8000)，[API 文档](http://127.0.0.1:8000/docs)，[运行状态](http://127.0.0.1:8000/health)。状态接口会区分 PostgreSQL、Redis 和 Worker。API 默认监听 127.0.0.1。

init-db 创建 MVP 数据表与 pgvector 扩展，可重复执行；创建扩展需要数据库用户有对应权限。正式部署前应换成明确版本的迁移管理。

## 使用流程

### 自定义字段与模板升级

字段和模板改造后，请先停止 API/Worker，运行 `.\.venv\Scripts\ainovel.exe init-db` 增加新表，再重启。旧小说和历史保留。

书架点击「管理字段模板」，或从侧栏进入「字段模板」：选择模板后直接显示可编辑字段，点击资料类别标签切换世界观、角色、状态、创作工作区、章节、幕、偏好和小说资料；点击「保存」更新当前模板，点击「新增模板」开始新的模板。无需先复制或打开抽屉，切换与刷新保留未保存输入。创建小说时选择一套，小说保存独立副本，后续新增角色、章节和幕自动初始化。

模板只提供初始化状态，本书字段的新增、删除、改名及其他修改都不会修改原模板。故事资料可以调整本书以后新增对象的初始化结构；也可以将当前资料的结构导出为全新的套装，角色、章节与幕支持选择一个对象作为样本。导出仅保存定义和填写解释，不带小说正文或填写内容。已有小说也可应用套装，保留已填写内容。

「故事资料」统一编辑递归字段，支持文本、数字、布尔、枚举、对象、列表及引用。原有世界观简介、偏好、主线、伏笔、AI 笔记等成为默认字段模板；可以复制默认模板、从零新建，或将当前字段定义另存为模板。模板只保存定义与解释，可以批量应用到多个角色、章节或幕，保留已有内容。

新修改永久保留增量 patch，每 20 次版本提交保存快照；读取时沿当前版本父链重放，快照不会清理旧 patch。故事资料提供变更历史。旧数据以已有版本快照为基线，兼容说明、接口和当前范围见 [自定义字段设计](docs/custom-fields.md)。

1. 点击「新作品」，回答世界观、人物、喜好与讨厌的写法。创建前会话作为「创作草稿」显示在书架；访谈、未发送输入、设定编辑和打开状态自动保存，刷新后继续。确认后才创建正式小说并归档草稿。构思清单会标明已定义和待讨论项；创建后的世界观、偏好、角色、剧情计划和写作要求集中在「故事资料」里继续维护。
2. 工作台共用一个顶部栏和可展开的窄侧栏。顶部可独立开关目录、小说和创作记录：全开时按左中右排列；只保留一个区域时占满工作区。输入框固定在底部并自动保存。创作记录按章节和幕关联，目录可切换关联位置；草稿确认后才写入正文，选文改写先预览再接受。AI 判断是否分章，也可在要求中指定。
确认阶段失败会保留草稿，再次确认优先重试原命令；同一版本、源故事和正文的角色事实与局部维护结果可复用，继续失败的后续步骤。新确认命令也检查已有失败结果，避免重复模型调用；已撤回或修改过的草稿不复用。每幕确认都会记录实际发生的人物与局部故事变化；全局大纲、主支线和长期笔记在首次创作、深入规划、新开章或每隔 `STORY_MAINTENANCE_INTERVAL` 幕整理一次（默认 5 幕）。

3. 「故事资料」里的写作要求可限定全局、后续故事、当前章节或下一个场景，支持硬约束与软偏好，不触发正文生成。下一个场景的要求提交后自动消费。
4. 正文中拖选句子或段落后点击「讨论修改」。也可修改整个场景或整章。修改先显示原文与建议，并排审阅后接受、重新生成或放弃。
5. 角色姓名、别名高亮，悬停只显示该场景的状态、心理和本幕变化，点击打开独立访谈页。阅读栏的「角色卡」打开完整角色页面，展示所有角色的档案、称呼及原因、自定义条目、经历和逐幕增量记录，可切换故事阶段；尚未登场的角色不在早期阶段显示。每个访谈会话固定到版本、角色和场景，不修改小说。
6. 生成器需要持续登场的新角色时，先请求引入许可，再生成可修改的档案，确认档案后自动继续。演示模式输入「新角色」或「NPC」可试用该流程。
7. 每幕末尾只显示 token、模型耗时、步骤及资料变更摘要。完整执行过程集中在会话内「本轮执行记录」，以小说为范围，支持切换小说和分页；列表标明创作章节与幕、讨论会话名称或角色会话。摘要、用量和变更分别以卡片展示，长回复和完整变更默认收起；以可读条目展示节点结果、工具、错误、模型返回的推理及逐次重试用量，隐藏空结果和重复提示词。原始持久日志保留。
8. 「删除小说」将当前作品放进回收站，取消未完成任务。书架的创作草稿卡片也提供「删除」，回收站统一显示小说和创作草稿，可以恢复。回收站内的「永久删除」会在确认后清除正文、角色、全部版本、字段历史、创建访谈、草稿、运行历史、作品提示词版本、检索索引及 LangGraph 检查点，不能恢复或撤回；全局模板和其他作品保留。后台任务仍在结束时需稍后重试，防止删除后写回。
升级需先执行 `ainovel init-db`，再重启 API/Worker。清理范围与并发处理见 [删除机制](docs/deletion.md)。
9. 顶栏可切换暗黑/浅色模式，工作台和独立角色页共用主题；首次跟随系统，主动切换后记住选择。
10. 所有撤回与失败恢复统一在「会话末尾」。只允许撤回当前作用域最新操作，随后才能撤回上一条；不能指定旧轮次批量撤回。覆盖创建、访谈、草稿、改写、角色审批和聊天、设定修改、删除恢复与索引等完整命令。小说共用一个栈，创建访谈按会话隔离。最新创作、讨论等生成操作还可重新生成或修改要求后重新生成：先记录撤回，再提交新命令，正文仍需草稿确认。新命令尚未被接受时，修改要求和已撤回状态会在浏览器中保存，刷新后可继续提交，避免重复撤回。

正在执行的操作必须先完成，不能撤回半次模型调用。等待角色审批的生成可撤回；撤回审批会恢复上一阶段与待确认档案，旧的队列投递不能越过等待状态。原命令、输出、版本与 token 日志保留，被撤回的聊天不再进入后续模型上下文。刷新或服务重启后撤回栈仍然保留。

失败记录在「会话末尾」处理，当前最新失败任务可从检查点重试或撤回。重试复用已保存结果并进行版本、事件校验；恢复决策仍为可撤回命令。历史、原始输出和费用记录不会被删除。

升级前的记录没有保存操作前凭据，不能可靠补出聊天/审批撤回；仍可用版本历史恢复小说。撤回恢复的是业务状态，已经发生的模型费用和耗时不会撤销。主题切换、打开页面等界面导航不进入小说操作栈。

## 会话与故事讨论

独立的作品运行历史页面已移除。每轮执行记录保存在对应的创作访谈、写作会话、故事讨论或角色访谈中，末尾消息提供回退、重新生成、修改、删除及失败重试。回退和删除同时恢复业务状态，重新生成和修改先恢复原操作，再提交新命令；已成文幕的设定锁定，只能重新生成文本，原规划和设定保留。

故事讨论只提供建议及按需查询前文，不修改任何资料或正文。资料修改集中在写幕工作区。只读讨论和角色访谈各有独立回退栈，不会覆盖其他会话或移动小说版本。具体规则见 [会话与回退](docs/conversations.md)。

## 前端开发

安装 Node.js 后，在仓库根目录安装锁定的前端依赖（Vue 3、TypeScript、Vite、Naive UI 和 Lucide 图标）。`openapi.json` 和 TypeScript API 类型从 FastAPI 的 Pydantic/OpenAPI schema 生成；改动 API 模型后先导出 schema，再生成类型。PowerShell 示例：

```powershell
npm ci
.\.venv\Scripts\python.exe scripts\export_openapi.py
npm run openapi
npm run typecheck
npm run build
```

开发中的 Vite 源码位于 `src/ainovel/frontend/`，构建产物由 FastAPI 静态资源路由提供；产品运行不需要单独启动 Node 开发服务器。页面主题由 `NConfigProvider` 和统一的明暗色令牌控制。

开发时可以运行 `npm run dev`，并另开终端启动 FastAPI；普通使用只需按上文启动 API 和 Worker，FastAPI 会提供 `src/ainovel/web/ui` 中的 Vue 构建文件。`npm run build` 输出需要随包发布；Python 包配置已包含 `web/ui` 及静态资源。

单页应用保留独立故事讨论和角色阶段会话。故事讨论只有会话列表及聊天记录各自滚动，输入框保持在聊天区底部；作品操作放在可展开侧栏，各入口显示当前选中状态；故事资料是独立页面，打开时不会跳回正文。人物页提供紧凑卡片及完整档案弹窗。完整 Jinja2 提示词源码编辑器继续放在高级设置，支持类型化上下文、作品/全局作用范围、版本和渲染预览；运行固定模板版本，历史日志保留实际提示词，普通界面不重复展示。

Vue 浏览器回归全部使用合成作品并拦截写请求：设置 `AINOVEL_BROWSER_TEST=1`、`AINOVEL_PLAYWRIGHT_PATH` 与 `AINOVEL_BROWSER_EXECUTABLE` 后，执行 `node --test tests/web_vue_*.test.cjs tests/web_workspace.test.cjs`。迁移前的原生页面浏览器脚本保留为历史用例，需显式设置 `AINOVEL_LEGACY_BROWSER_TEST=1` 才会运行，不计入 Vue 回归结果。

## 接入真实模型

.env 中设置：

```dotenv
LLM_PROVIDER=openai
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_API_KEY=你的密钥
LLM_MODEL=deepseek-flash
LLM_THINKING=disabled
EMBEDDING_PROVIDER=hash
EMBEDDING_DIMENSIONS=1536
```

重启 API 和 Worker。openai 是协议名称，既可连接 DeepSeek，也可连接其他兼容 /chat/completions 的服务。支持 OPENAI_BASE_URL / OPENAI_API_KEY 与 LLM_BASE_URL / LLM_API_KEY；同时存在时 LLM_* 优先。DeepSeek 使用 JSON 输出和显式 thinking 开关，返回数据继续经过 Pydantic 校验，缓存 token 支持 DeepSeek 的用量字段。参见 [DeepSeek 官方 API 文档](https://api-docs.deepseek.com/api/create-chat-completion/)。

当前配置使用 deepseek-flash 写作；此前接入已验证访谈、角色咨询、写作、Judge 与提交。本次结构化长期记忆、子图编排和草稿审阅流程使用 Mock 配合真实 PostgreSQL/Redis 验证，未额外调用收费模型。LLM_PROVIDER=mock 仍可离线演示；它不代表真实写作质量，也不返回真实 token 数。

embedding 独立于聊天模型。当前 EMBEDDING_PROVIDER=hash 本地运行，保留已有演示索引兼容性；哈希向量不具备真实语义理解。需要语义向量时，设置 EMBEDDING_PROVIDER=openai、EMBEDDING_BASE_URL、EMBEDDING_API_KEY、EMBEDDING_MODEL 和 EMBEDDING_DIMENSIONS，连接提供 /embeddings 的服务，不复用 DeepSeek 的聊天地址。

从 mock 切换到真实模型，或更改向量维度/embedding 模型后，在「版本历史」点击「用当前模型重建检索索引」，再生成新内容。该 reindex 命令保留正文与角色数据，产生一个使用新向量的新版本。旧版本不就地改动；回退旧版本时也会使用当前向量模型重新索引。

## 已实现与保留接口

| 能力 | MVP 实现 |
| --- | --- |
| 创建访谈 | 多轮结构化对话，设定可修改，显式确认创建 |
| 写作 Agent | 三个前置子图：意图与规划、可选召回、可选角色处理；写作子图产出草稿，HITL 确认后进入更新子图；PostgreSQL 检查点恢复 |
| 草稿审阅 | 先预览、聊天反馈、局部选区修改、版本校验、确认/放弃；确认前不更新正式正文与记忆，支持撤回 |
| 角色记忆 | 可演化的结构化档案与别名、时变 State、增量 Experience；基线 + 场景补丁回放 |
| 持续设定 | 世界观简介与开放条目、角色档案和阶段条目、偏好、主线/支线/伏笔/大纲/笔记/草稿；AI 维护与人工命令编辑 |
| 故事讨论 | 独立会话列表、最新故事上下文、按需召回、原生工具修改设定；整轮原子提交与撤回，不修改正文 |
| 检索 | 章节摘要向量 → 章节内段落；全库向量；去重后时间排序；最近场景直接注入 |
| 角色召回 | jieba 中文分词 + BM25，名字/别名/事件关键词，限于当时已知经历 |
| 改写 | 句段、场景、整章；建议预览与显式接受；偏移按 Unicode 码点计算 |
| 版本 | 不可变全量聚合快照，乐观锁，历史分支，回退形成新版本 |
| 命令与日志 | 类型化命令处理器、ID 幂等、数据库审计、父子图持久 checkpoint |
| 连续撤回 | UndoRequest + UndoHandler，持久操作栈，Pydantic Memento，与操作效果同一事务写入；支持并发冲突和重复投递 |
| 失败恢复 | RecoverCommandRequest + RecoverCommandHandler；显式重试或撤回失败，检查点恢复、版本校验、事件校验和逐次日志 |
| Queue/SSE | Redis 队列，PostgreSQL outbox 补投，SSE 持久事件重播、心跳 |
| 知识图谱 | KnowledgeGraph 协议与 NullKnowledgeGraph，暂不计算关系图/剧情图 |
| 删除与恢复 | 回收站、历史保留、未完成任务取消、并发生成写入保护 |
| 章节与主题 | AI/自然语言分章、全页面暗黑模式、主题持久化 |

思考展示使用行动和决策摘要；不依赖或存储模型内部完整推理链。SSE 当前推送步骤和结果，不逐 token 输出正文。

## 目录

```text
src/ainovel/
  core/           配置、Pydantic 领域契约、Protocol、业务异常
  controller/     FastAPI 路由、SSE、静态资源响应
  service/        命令处理器、LangGraph、撤回、检索、Worker
  mapper/         ORM 表、Pydantic 序列化、事务持久化、草稿与向量查询
  llm/            供应商协议、OpenAI/DeepSeek 客户端、Mock、向量适配器
  infrastructure/ Redis 队列、持久日志、PostgreSQL 图检查点、预留知识图谱适配器
  utils/          无 I/O 的段落切块、时间工具
  bootstrap/      Runtime 依赖组装与资源管理
  cli.py          init-db / api / worker 的应用入口
  web/            工作台、独立角色会话页
tests/            领域、供应商协议与真实基础设施集成测试
docs/
  architecture.md 数据语义、扩展约定与已知取舍
  workspace.png   浏览器验证截图
```

领域与业务层不使用 dict / TypedDict 作为数据类型。第三方 HTTP headers、JSON Schema、LangGraph 的配置/传输边界仍遵循各库的映射格式，离开边界后立即使用 Pydantic。

真实模型统一使用 `llm/service.py` 的 `LLMService`。创作、结构化生成、讨论工具与普通回复共用 `ModelGateway` 的解析、有限重试、用量和审计管线，向量请求复用同一传输入口。`structured.py` 和 `discussion.py` 只处理模式策略，不再分别请求供应商；业务依赖 `LanguageModel`，传输可通过 `ModelTransport` 替换或注入测试。

新增 HTTP 接口放在 `controller`，业务操作通过 `service/commands.py` 的 CommandBus 执行；数据查询与原子持久化放在 `mapper`。`core` 不依赖 Web、数据库或模型 SDK；`llm` 通过 `core/ports.py` 的接口接入，依赖组装集中在 `bootstrap/runtime.py`。具体文件职责见 `docs/architecture.md`。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q

# 可选：实际调用已配置的 DeepSeek；仅使用合成素材，调用会产生供应商用量
$env:AINOVEL_LIVE_LLM = "1"
.\.venv\Scripts\python.exe -m pytest tests/test_live_deepseek.py -q
.\.venv\Scripts\python.exe -m ruff check src tests

# 对接本地 PostgreSQL + Redis；使用同一个数据库时先暂停其他 Worker
$env:AINOVEL_INTEGRATION = "1"
.\.venv\Scripts\python.exe -m pytest -q
```

集成测试仅删除自身创建并记录 ID 的小说、命令、索引和事件，不清空数据库。日常运行建议给测试配置独立数据库与 Redis logical DB。DATABASE_URL、REDIS_URL 支持环境变量覆盖。

自动测试覆盖以下场景（真实模型测试默认跳过）。包含 PostgreSQL/pgvector/Redis 集成、删除恢复、正在生成时删除、AI 分章、DeepSeek 协议兼容，以及连续撤回、NPC 检查点恢复、聊天上下文排除、建议恢复、并发撤回和幂等；另覆盖草稿跨 Runtime 恢复、旧访谈发现、草稿归档、过期自动保存、旧轮次撤回拒绝与逐次顺序撤回，以及开放嵌套条目、旧字段和历史补丁兼容、指定结果工具、错误工具与重复调用拒绝、参数校验、截断的有限重试和逐次审计，以及失败任务撤回、手动重试、重复请求拦截和阶段角色重试。另覆盖角色卡的阶段回放、晚登场角色隐藏与完整条目投影。实际 DeepSeek 冒烟测试在之前的接入验证中通过。浏览器验证包含创建、改写、角色访谈、主题切换、回收站删除恢复、连续撤回，以及未发送输入和设定表单刷新恢复、最新操作的连续撤回；浏览器回归另验证结果过程记录分页与隔离、刷新恢复、悬停信息和角色卡阶段切换，以及节点合并、空值过滤、有效否/零保留、返回推理展示、工具参数解析和无效响应隔离。

## MVP 的明确取舍

- 版本保存完整类型化聚合，便于原子提交和回退；新版本重新索引全文。适合验证阶段，长篇场景需改为共享的不可变实体、增量索引。
- pgvector 当前精确扫描，未配置 HNSW。检索和索引都限制到源 revision，避免分支混读。
- 历史修改截断修改处之后的场景。整章改写合并该章为一个新 Scene，再从章尾继续。旧 Scene、Snapshot 和后续章节仍保留在旧 revision。
- 角色聊天固定到 Scene 结束时的快照；句子中的点击也使用该 Scene 的状态，未实现句级心理时间线。
- 创作备注支持 scene/chapter/future/global；选区要求在改写命令中指定，尚未提供永久的 selection 备注。
- PostgreSQL 的命令表充当持久 outbox；Redis 负责解耦执行。崩溃后可重做尚未提交的模型步骤，收费调用不能保证只执行一次，正文提交可以保证幂等。
- 当前是本地单用户 MVP，未实现账号权限、多人协作、全文自动修复、真实图谱或逐 token 流式生成。

新增草稿验证覆盖确认前正式状态隔离、重启恢复、选区外文本保留、过期版本拒绝、确认/修改/放弃撤回、维护失败恢复，以及草稿修改中新增 NPC 的嵌套审批；浏览器契约测试拦截所有写请求，验证预览、刷新、选区反馈、聊天反馈及确认按钮，不改动用户小说。

草稿待确认时，整幕修改与选区修改默认复用生成该幕时的要求、规划、前文、召回结果和角色访谈。修订判断可以独立启用重新规划、补充召回、重新访谈和独立审阅，也可以全部启用；补充召回会与原有结果合并、去重并排序。局部改写仍严格保留选区外正文，新角色仍需审批，正式资料仅在确认草稿后更新。

未指定选区的草稿修改由 AI 自行选择句子或段落，通过结构化工具返回原文、替换正文和原因。后端逐项检查唯一定位和范围冲突，全部通过后原子应用，其他正文与排版保持原样；普通修改不重写整幕，明确重写整幕或贯穿整幕的改动仍可使用整幕替换。重复片段需附带相邻正文定位，定位错误不会部分修改草稿。

修改列表存在重叠、重复定位或原文不匹配时，将具体错误反馈给写作模型，按 `LLM_OUTPUT_RETRIES` 有限重试（默认两次）；仅修复修改列表，不重跑召回和访谈。每次冲突与模型调用都保留在运行记录中。修复失败仍保留原草稿，运行历史支持重试或撤回。
