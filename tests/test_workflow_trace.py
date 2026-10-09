from uuid import uuid4

from ainovel.core.domain import Draft, Plan, Story, WorkflowState
from ainovel.service.workflow_trace import node_input, node_output


def test_node_projection_separates_input_and_output_and_keeps_negative_decisions() -> None:
    state = WorkflowState(command_id=uuid4(), story=Story(title="来信"), instruction="不要另起一章")
    state.plan = Plan(chapter_plan="继续", scene_plan="读信", character_names=[], start_new_chapter=False)
    inputs = node_input(state, "plan")
    outputs = node_output(state, "plan")
    assert inputs.instruction == "不要另起一章" and inputs.plan is None
    assert outputs.plan == state.plan and outputs.instruction is None
    assert '"start_new_chapter":false' in outputs.model_dump_json(exclude_none=True)
    assert "graph_checkpoint_id" not in inputs.model_dump_json()


def test_write_projection_records_actual_prose_and_skip_has_no_artificial_output() -> None:
    state = WorkflowState(command_id=uuid4(), story=Story(title="来信"), instruction="继续")
    state.draft = Draft(title="窗边", text="林初拆开信。", summary="收到来信")
    assert node_output(state, "write").draft == state.draft
    assert node_output(state, "write").writer is None
    assert node_output(state, "skip_characters").draft_status is None
