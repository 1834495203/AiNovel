# 提示词、上下文布局与 AI Schema

每个任务分别保存 `source`（行为提示词 Jinja2）和 `context_source`（上下文布局 Jinja2），前端的「提示词与上下文」可分别编辑、预览和保存。书架支持编辑全局默认；幕设定页支持编辑当前作品。上下文模板随版本固定到运行，重试不重新解析活动模板。

行为提示词资源位于 `src/ainovel/prompts/*.j2`。默认上下文布局位于 `src/ainovel/prompts/context.j2`，通过 `<!-- section -->` 拆分独立资料消息。可用的分块变量是 `world_context`、`characters_context`、`preferences_context`、`writer_context`、`scene_plans_context`、`recent_story_context` 与 `task_context`，均可在前端预览。变量来自显式投影，不暴露服务对象或配置。

`core/context_layout.py` 负责字段值投影，移除模板定义和存储元数据。较大的初始资料段提供带截取标记的预览。幕设定 agent 可调用 `read_story_materials`，按资料类别、对象和分页读取相关完整条目，无需始终发送全部档案。正文等结构化任务也通过 Jinja 渲染任务所需资料，正文与修改意见保留在专属任务段。

Schema 独立于 Jinja：

- `core/discussion.py` 中的 Pydantic 参数与公开返回模型使用 `Field(description=...)` 解释含义和边界。
- `core/ai_contracts.py` 注册工具，通过 `model_json_schema()` 导出参数和返回值；允许显式覆盖工具 description。
- 用户自定义字段的类型、选项与 description 从 `FieldDefinition` 导出为 JSON Schema，以 `$defs` 和 `$ref` 去重，仅绑定本次实际发送的字段 ID。
- `GET /api/ai-contracts` 导出工具、返回值及结构化结果 schema。前端在独立的 Schema 标签中查看，schema 不复制进编辑器源码。

发送请求时，Jinja 行为消息、独立 schema 说明、分块资料消息、会话历史和本轮用户输入分别组装。可执行工具参数只通过原生 `tools[].function.parameters` 提供，不在自然语言提示词中重复列举。工具调用和结果继续使用原生 assistant/tool 消息及调用 ID 配对。

行为模板只描述任务目标、叙事约束与操作边界，不列举内部结果键或要求补齐固定资料栏目。内部结果格式由 Pydantic description 和校验器约束；这些协议字段不属于小说自定义字段，也不会因此初始化小说资料。
