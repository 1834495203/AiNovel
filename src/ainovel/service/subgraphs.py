"""Independent graph boundaries share a typed state and inherit the parent checkpointer."""

from typing import TYPE_CHECKING

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ainovel.core.domain import WorkflowState

if TYPE_CHECKING:
    from ainovel.service.workflow import NarrativeWorkflow


def planning_graph(nodes: "NarrativeWorkflow") -> CompiledStateGraph:
    graph = StateGraph(WorkflowState)
    graph.add_node("analyze", nodes.analyze)
    graph.add_node("context", nodes.prepare_context)
    graph.add_node("plan", nodes.plan)
    graph.add_node("quick_plan", nodes.quick_plan)
    graph.add_node("rewrite_plan", nodes.rewrite_plan)
    graph.add_edge(START, "context")
    graph.add_edge("context", "analyze")
    graph.add_conditional_edges("analyze", nodes.planning_route)
    for name in ("plan", "quick_plan", "rewrite_plan"):
        graph.add_edge(name, END)
    return graph.compile()


def recall_graph(nodes: "NarrativeWorkflow") -> CompiledStateGraph:
    graph = StateGraph(WorkflowState)
    graph.add_node("retrieve", nodes.retrieve)
    graph.add_node("skip_retrieval", nodes.skip_retrieval)
    graph.add_conditional_edges(START, nodes.recall_route)
    graph.add_edge("retrieve", END)
    graph.add_edge("skip_retrieval", END)
    return graph.compile()


def characters_graph(nodes: "NarrativeWorkflow") -> CompiledStateGraph:
    graph = StateGraph(WorkflowState)
    graph.add_node("approval", nodes.approval)
    graph.add_node("consult", nodes.consult)
    graph.add_node("skip_characters", nodes.skip_characters)
    graph.add_conditional_edges(START, nodes.characters_route)
    graph.add_conditional_edges("approval", nodes.after_approval)
    graph.add_edge("consult", END)
    graph.add_edge("skip_characters", END)
    return graph.compile()


def writing_graph(nodes: "NarrativeWorkflow") -> CompiledStateGraph:
    graph = StateGraph(WorkflowState)
    graph.add_node("write", nodes.write)
    graph.add_node("judge", nodes.judge)
    graph.add_edge(START, "write")
    graph.add_conditional_edges("write", nodes.writing_route)
    graph.add_conditional_edges("judge", nodes.after_judge)
    return graph.compile()


def finalization_graph(nodes: "NarrativeWorkflow") -> CompiledStateGraph:
    graph = StateGraph(WorkflowState)
    graph.add_node("extract", nodes.extract)
    graph.add_node("maintain", nodes.maintain)
    graph.add_edge(START, "extract")
    graph.add_edge("extract", "maintain")
    graph.add_edge("maintain", END)
    return graph.compile()
