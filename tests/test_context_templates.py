from uuid import uuid4

from configured_fields import make_world

from ainovel.core.ai_contracts import agent_tools
from ainovel.core.characters import Profile, Snapshot
from ainovel.core.config import Settings
from ainovel.core.discussion import DiscussionCharacter, DiscussionContext, DiscussionStory
from ainovel.core.domain import Draft, PromptTemplateVersion
from ainovel.core.records import Entry, ReaderPreferences, WriterWorkspace
from ainovel.llm.agent import build_agent_request
from ainovel.llm.structured import build_structured_request


def test_saved_jinja_controls_agent_role_and_separate_context_layout():
    character_id = uuid4()
    context = DiscussionContext(
        planning_mode=True,
        story=DiscussionStory(
            title="港口",
            revision_id=uuid4(),
            world=make_world("潮汐决定封港时间"),
            preferences=ReaderPreferences(),
            writer=WriterWorkspace(),
            characters=[
                DiscussionCharacter(
                    id=character_id,
                    name="沈砚",
                    snapshot=Snapshot(
                        character_id=character_id,
                        profile=Profile(entries=[Entry(name="动机", value="寻找哥哥")]),
                    ),
                )
            ],
            custom_fields=[],
            recent_scenes=[],
        ),
        history=[],
        message="规划下一幕",
    )
    version = PromptTemplateVersion(
        id=uuid4(),
        scope="global",
        scope_id=uuid4(),
        task="discussion",
        name="自定义",
        version=1,
        source_format="jinja2",
        source="角色：{{ story_title }}",
        context_source="世界={{ world_context }}<!-- section -->人物={{ characters_context }}",
    )
    body = build_agent_request(Settings(_env_file=None), context, version).body
    assert body.messages[0].content == "角色：港口"
    assert "tool_returns" in body.messages[1].content
    assert body.messages[2].content.startswith("世界=") and "潮汐" in body.messages[2].content
    assert "寻找哥哥" in body.messages[3].content
    assert body.messages[-1].content == "规划下一幕"
    assert "definition" not in body.messages[2].content
    assert "parameters" not in body.messages[0].content


def test_structured_writing_uses_the_saved_context_template():
    from ainovel.core.domain import GenerateRequest

    version = PromptTemplateVersion(
        id=uuid4(),
        scope="global",
        scope_id=uuid4(),
        task="writing",
        name="自定义",
        version=1,
        source_format="jinja2",
        source="写正文",
        context_source="本次输入：{{ task_context }}",
    )
    body = build_structured_request(
        Settings(_env_file=None), Draft, "writing", GenerateRequest(instruction="保留人物动机"), version
    ).body
    assert body.messages[0].content == "写正文"
    assert body.messages[-1].content.startswith("本次输入：")
    assert "保留人物动机" in body.messages[-1].content
    assert body.tools[0].function.parameters.root == Draft.model_json_schema()
    assert "parameters" not in body.messages[-1].content


def test_tools_export_descriptions_and_return_schemas_outside_prompt_text():
    for tool in agent_tools():
        assert tool.description
        assert tool.parameters.get("description")
        assert tool.returns.get("description")
        for field in tool.parameters.get("properties", {}).values():
            assert field.get("description")
    custom = __import__("ainovel.core.ai_contracts", fromlist=["tool_contract"]).tool_contract
    from ainovel.core.discussion import SearchStoryArguments, ToolResult

    assert (
        custom("search", SearchStoryArguments, ToolResult, "自定义工具说明").description == "自定义工具说明"
    )


def test_user_field_descriptions_export_once_as_schema_not_value_metadata():
    from ainovel.core.context_layout import clean, custom_field_schemas
    from ainovel.core.fields import FieldDefinition
    definition=FieldDefinition(name="人物原则",description="说明人物不愿跨越的底线。")
    a=Entry(name="人物原则",value="不伤害同伴",definition=definition)
    b=Entry(name="人物原则",value="不出卖朋友",definition=definition)
    document={"entries":[a.model_dump(mode="json"),b.model_dump(mode="json")]}
    schema=custom_field_schemas(document,{str(a.id)})
    assert len(schema['$defs']) == 1
    assert schema['$defs'][str(definition.id)]['description'] == definition.description
    assert list(schema['properties']) == [str(a.id)]
    assert 'definition' not in clean(document)['entries'][0]
    assert definition.description not in str(clean(document))


def test_builtin_behavior_prompts_do_not_prescribe_internal_field_keys():
    import re

    from ainovel.core.domain import PROMPT_TASKS
    from ainovel.core.prompting import default_prompt_source
    banned={"judge","feedback","draft","profile","snapshot","memories","history","original","selection","instruction","edits","replacement","proposal","checklist"}
    for task in PROMPT_TASKS:
        source=default_prompt_source(task)
        prose=re.sub(r"\{%.*?%\}|\{\{.*?\}\}","",source,flags=re.S)
        assert not re.search(r"\b[a-z]+_[a-z_]+\b",prose),task
        assert not banned.intersection(re.findall(r"\b[a-z]+\b",prose)),task
    # Internal result keys stay in their schema, never become novel fields.
    from ainovel.core.domain import Plan, Story
    assert 'scene_plan' in Plan.model_json_schema()['properties']
    assert Story(title="自定义结构").writer.entries == []
