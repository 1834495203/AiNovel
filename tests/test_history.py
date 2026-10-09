"""The journal and strict sequential undo use real PG with synthetic fixture records."""

import os

import httpx
import pytest
from test_integration import Harness

from ainovel.controller.api import create_app
from ainovel.core.domain import Direction, DirectionRequest, UndoRequest
from ainovel.service.history import project_history
from ainovel.service.undo import UndoService

pytestmark = pytest.mark.skipif(os.getenv("AINOVEL_INTEGRATION") != "1", reason="Local PostgreSQL/Redis")


async def test_journal_scope_pagination_and_only_latest_operation_can_be_undone() -> None:
    h = Harness()
    await h.runtime.store.initialize()
    try:
        await h.create()
        initial = h.head
        first = await h.execute(DirectionRequest(direction=Direction(instruction="第一条要求")))
        second = await h.execute(DirectionRequest(direction=Direction(instruction="第二条要求")))
        page = await project_history(h.runtime.store, h.story_id, 0, 2)
        assert page.total == 3 and len(page.items) == 2
        assert page.latest.command_id == second.submission.id
        assert page.items[0].command.submission.id == second.submission.id
        assert page.items[0].story_title.startswith("集成测试-")
        rejected = await h.execute(
            UndoRequest(expected_operation_id=page.latest.operation_id, target_command_id=first.submission.id)
        )
        assert rejected.status == "failed" and "只能撤回最新操作" in rejected.error
        assert not (await h.runtime.store.command(first.submission.id)).undone
        for target in [second, first]:
            state = await UndoService(h.runtime.store).state(h.story_id)
            assert state.command_id == target.submission.id
            result = await h.execute(
                UndoRequest(expected_operation_id=state.operation_id, target_command_id=state.command_id)
            )
            assert result.status == "completed", result.error
            assert result.output.undone_command_ids == [target.submission.id]
        assert h.head == initial
    finally:
        await h.cleanup()


async def test_history_http_contract_and_deleted_story_audit_remain_readable() -> None:
    from ainovel.core.domain import DeleteStoryRequest

    h = Harness()
    await h.runtime.store.initialize()
    app = create_app(h.runtime.settings, h.queue)
    try:
        await h.create()
        deleted = await h.execute(DeleteStoryRequest())
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
        ):
            response = await client.get("/api/history", params={"scope_id": str(h.story_id), "limit": 1})
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["total"] == 2
            assert data["latest"]["command_id"] == str(deleted.submission.id)
            assert data["latest"]["revision_id"] == str(h.head)
            assert data["items"][0]["command"]["submission"]["payload"]["kind"] == "delete_story"
            assert (await client.get("/api/history?limit=101")).status_code == 422
    finally:
        await h.cleanup()


async def test_repeated_recovery_failure_keeps_original_task_as_latest_retry_target() -> None:
    from test_recovery import fail_once, request

    from ainovel.core.domain import GenerateRequest

    h = Harness(auto_confirm=False)
    await h.runtime.store.initialize()
    try:
        await h.create()
        fail_once(h)
        failed = await h.execute(GenerateRequest(instruction="写第一幕"))
        assert failed.status == "failed"
        recovery = await h.execute(await request(h, failed.submission.id))
        assert recovery.status == "completed"
        fail_once(h)
        await h.worker.process(failed.submission.id)
        assert (await h.runtime.store.command(failed.submission.id)).status == "failed"
        page = await project_history(h.runtime.store, h.story_id, 0, 50)
        assert page.latest_command_id == failed.submission.id
        assert any(item.command.submission.id == recovery.submission.id for item in page.items)
    finally:
        await h.cleanup()
