"""Render named data sections with the saved context template, independently of schemas."""
from ainovel.core.context_layout import sections_from
from ainovel.core.prompting import PromptRuntimeContext, render_prompt_source
from ainovel.llm.schemas import Message


def context_messages(task, template, data, runtime=None):
    runtime = (runtime or PromptRuntimeContext()).model_copy(update=sections_from(data))
    source = template.context_source
    rendered = render_prompt_source(task, source, runtime)
    return [Message(role="user",content=part.strip()) for part in rendered.text.split("<!-- section -->") if part.strip()]
