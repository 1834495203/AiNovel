"""Immutable saved template versions; undo hides a version, never destroys it."""

from uuid import UUID

from sqlalchemy import select

from ainovel.core.domain import CommandOutput, CommandSubmission, Event
from ainovel.core.errors import ConflictError
from ainovel.core.field_templates import default_template_set, default_templates
from ainovel.core.fields import FieldTemplate, FieldTemplateSet
from ainovel.mapper.models import CommandRow, EventRow, FieldTemplateRow, FieldTemplateSetRow
from ainovel.mapper.store import Store


class FieldTemplateRepository:
    def __init__(self, store: Store) -> None:
        self.store = store

    async def set_versions(self) -> list[FieldTemplateSet]:
        async with self.store.sessions() as session:
            rows = await session.scalars(select(FieldTemplateSetRow).join(
                CommandRow, CommandRow.id == FieldTemplateSetRow.command_id
            ).where(CommandRow.undone_at.is_(None), CommandRow.status == "completed").order_by(CommandRow.created_at))
            return [default_template_set(), *[row.payload for row in rows]]

    async def list_sets(self) -> list[FieldTemplateSet]:
        versions = await self.set_versions()
        replaced = {item.replaces_id for item in versions if item.replaces_id}
        positions: dict[UUID, int] = {}
        for index, item in enumerate(versions):
            positions[item.id] = positions.get(item.replaces_id, index)
        return sorted((item for item in versions if item.id not in replaced), key=lambda item: positions[item.id])

    async def get_set(self, template_set_id: UUID) -> FieldTemplateSet:
        versions = await self.set_versions()
        result = next((item for item in versions if item.id == template_set_id), None)
        if result is None:
            raise ValueError("小说模板套装不存在或已撤回")
        visited = set()
        while result.id not in visited:
            visited.add(result.id)
            newer = next((item for item in versions if item.replaces_id == result.id), None)
            if newer is None:
                return result
            result = newer
        raise ValueError("模板版本链存在循环")

    async def save_set(self, submission: CommandSubmission, template_set: FieldTemplateSet, replace_id: UUID | None = None) -> CommandOutput:
        if template_set.builtin or template_set.id == default_template_set().id:
            raise ValueError("默认小说模板不可覆盖，请另存为新模板")
        template_set = template_set.model_copy(deep=True)
        if template_set.replaces_id is not None:
            raise ValueError("请通过保存目标指定要修改的模板")
        template_set.replaces_id = replace_id
        scopes = {template.scope for template in template_set.templates}
        template_set.templates.extend(item for item in default_templates() if item.scope not in scopes)
        output = CommandOutput(field_template_set=template_set, message="模板已保存" if replace_id else "新模板已创建")
        async with self.store.sessions.begin() as session:
            await self.store.record_operation(session, submission)
            if replace_id is not None:
                # The scope lock serializes edits. Never silently overwrite a newer edit.
                current = await self.get_set(replace_id)
                if current.id != replace_id:
                    raise ConflictError("模板已被其他操作修改，请刷新后重试；当前输入仍保留")
            command = await session.get(CommandRow, submission.id, with_for_update=True)
            if command is None or command.status != "running" or command.undone_at:
                raise ConflictError("模板保存任务已停止")
            if await session.get(FieldTemplateSetRow, template_set.id):
                raise ConflictError("模板套装 ID 已存在，请另存为新版本")
            session.add(FieldTemplateSetRow(id=template_set.id, command_id=submission.id, payload=template_set))
            command.output = output
            command.status = "completed"
            session.add(EventRow(command_id=submission.id, payload=Event(
                command_id=submission.id, type="run.completed", output=output,
            )))
        return output

    async def list(self) -> list[FieldTemplate]:
        async with self.store.sessions() as session:
            rows = await session.scalars(
                select(FieldTemplateRow)
                .join(CommandRow, CommandRow.id == FieldTemplateRow.command_id)
                .where(CommandRow.undone_at.is_(None), CommandRow.status == "completed")
                .order_by(CommandRow.created_at)
            )
            return [*default_templates(), *[row.payload for row in rows]]

    async def get(self, template_id: UUID) -> FieldTemplate:
        template = next((item for item in await self.list() if item.id == template_id), None)
        if template is None:
            raise ValueError("字段模板不存在或已撤回")
        return template

    async def save(self, submission: CommandSubmission, template: FieldTemplate) -> CommandOutput:
        if template.builtin or any(item.id == template.id for item in default_templates()):
            raise ValueError("默认模板不可覆盖，请另存为自定义模板")
        if template.parent_id:
            parent = await self.get(template.parent_id)
            if parent.scope != template.scope:
                raise ValueError("模板版本不能改变适用范围")
        output = CommandOutput(field_template=template, message="字段模板已保存")
        async with self.store.sessions.begin() as session:
            await self.store.record_operation(session, submission)
            command = await session.get(CommandRow, submission.id, with_for_update=True)
            if command is None or command.status != "running" or command.undone_at:
                raise ConflictError("模板保存任务已停止")
            existing = await session.get(FieldTemplateRow, template.id)
            if existing:
                raise ConflictError("模板 ID 已存在，请以新版本保存")
            session.add(FieldTemplateRow(id=template.id, command_id=submission.id, payload=template))
            command.output = output
            command.status = "completed"
            session.add(
                EventRow(
                    command_id=submission.id,
                    payload=Event(command_id=submission.id, type="run.completed", output=output),
                )
            )
        return output
