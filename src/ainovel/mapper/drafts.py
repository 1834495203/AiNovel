"""Durable setup drafts; dialogue is reconstructed from its existing command audit."""

from uuid import UUID

from sqlalchemy import select

from ainovel.core.domain import CommandOutput, CommandSubmission, Event, SaveSetupDraftRequest, SetupDraft
from ainovel.core.errors import ConflictError
from ainovel.mapper.models import CommandRow, EventRow, SetupDraftRow
from ainovel.mapper.store import Store


class DraftRepository:
    def __init__(self, store: Store) -> None:
        self.store = store

    async def save(self, submission: CommandSubmission, request: SaveSetupDraftRequest) -> CommandOutput:
        async with self.store.sessions.begin() as session:
            await self.store.lock_operation_scope(session, request.draft.id)
            command = await session.get(CommandRow, submission.id, with_for_update=True)
            if command is None or command.status != "running":
                raise ConflictError("草稿保存任务已停止")
            row = await session.get(SetupDraftRow, request.draft.id, with_for_update=True)
            accepted = await session.scalar(
                select(EventRow).where(EventRow.command_id == submission.id).order_by(EventRow.id).limit(1)
            )
            if accepted is None:
                raise ConflictError("草稿保存请求缺少接收记录")
            draft = request.draft.model_copy(
                update={"updated_at": command.created_at, "save_event_id": accepted.id}
            )
            if row is None:
                session.add(SetupDraftRow(id=draft.id, payload=draft))
            elif (row.payload.save_event_id > 0 and row.payload.save_event_id < accepted.id) or (
                row.payload.save_event_id == 0 and row.payload.updated_at < command.created_at
            ):
                row.payload = draft
            output = CommandOutput(draft_id=draft.id, message="草稿已保存")
            command.output = output
            command.status = "completed"
            # Autosave is a workspace projection, not an extra dialogue/undo step.
            session.add(
                EventRow(
                    command_id=submission.id,
                    payload=Event(command_id=submission.id, type="run.completed", output=output),
                )
            )
            return output

    async def list(self) -> list[SetupDraft]:
        async with self.store.sessions() as session:
            rows = (await session.scalars(select(SetupDraftRow))).all()
            commands = (
                await session.scalars(
                    select(CommandRow).where(CommandRow.story_id.is_(None)).order_by(CommandRow.created_at)
                )
            ).all()
            ids = {row.id for row in rows} | {
                row.payload.session_id
                for row in commands
                if row.payload.session_id and row.payload.payload.kind == "interview"
            }
            drafts: list[SetupDraft] = []
            for draft_id in ids:
                related = [
                    row for row in commands if row.payload.session_id == draft_id and row.undone_at is None
                ]
                if any(
                    row.payload.payload.kind == "create_story" and row.status == "completed"
                    for row in related
                ):
                    continue
                saved = next((row.payload for row in rows if row.id == draft_id), None)
                interviews = [row for row in related if row.output.interview and row.status == "completed"]
                draft = saved.model_copy(deep=True) if saved else SetupDraft(id=draft_id)
                if interviews and (saved is None or interviews[-1].created_at > saved.updated_at):
                    latest = interviews[-1]
                    draft.proposal = latest.output.interview.proposal
                    if draft.proposal:
                        draft.title = draft.proposal.title
                    draft.updated_at = latest.created_at
                drafts.append(draft)
            return sorted(drafts, key=lambda item: item.updated_at, reverse=True)

    async def get(self, draft_id: UUID) -> SetupDraft:
        draft = next((item for item in await self.list() if item.id == draft_id), None)
        if draft is None:
            raise ValueError("草稿不存在或已创建为小说")
        return draft
