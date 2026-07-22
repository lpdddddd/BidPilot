"""Compile the BidPilot LangGraph StateGraph for the business loop."""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.agent import routing
from app.agent.checkpoint import new_memory_saver
from app.agent.nodes import (
    extract_requirements_node,
    finalize_run,
    generate_response_draft,
    initialize_run,
    load_project_context,
    match_company_evidence_node,
    retrieve_evidence,
    revise_draft,
    run_compliance_check,
    validate_draft,
)
from app.agent.nodes._helpers import AgentInterrupt, _RUNTIME
from app.agent.state import (
    NODE_COMPLIANCE,
    NODE_DRAFT,
    NODE_EXTRACT,
    NODE_FINALIZE,
    NODE_INITIALIZE,
    NODE_LOAD_CONTEXT,
    NODE_MATCH,
    NODE_RETRIEVE,
    NODE_REVISE,
    NODE_VALIDATE,
    AgentState,
)


def build_graph(*, checkpointer: MemorySaver | None = None) -> Any:
    """Build and compile the agent StateGraph.

    Nodes orchestrate only — business work is delegated to tools/services.
    """
    g: StateGraph = StateGraph(AgentState)

    def _wrap(fn):
        def inner(state: AgentState) -> AgentState:
            out = fn(state)
            runtime = _RUNTIME.get()
            if runtime is not None:
                runtime.last_state = dict(out)
            if out.get("interrupt_requested"):
                raise AgentInterrupt(str(out.get("current_node") or "unknown"))
            return out

        return inner

    g.add_node(NODE_INITIALIZE, _wrap(initialize_run))
    g.add_node(NODE_LOAD_CONTEXT, _wrap(load_project_context))
    g.add_node(NODE_RETRIEVE, _wrap(retrieve_evidence))
    g.add_node(NODE_EXTRACT, _wrap(extract_requirements_node))
    g.add_node(NODE_MATCH, _wrap(match_company_evidence_node))
    g.add_node(NODE_COMPLIANCE, _wrap(run_compliance_check))
    g.add_node(NODE_DRAFT, _wrap(generate_response_draft))
    g.add_node(NODE_VALIDATE, _wrap(validate_draft))
    g.add_node(NODE_REVISE, _wrap(revise_draft))
    g.add_node(NODE_FINALIZE, _wrap(finalize_run))

    g.add_edge(START, NODE_INITIALIZE)
    g.add_conditional_edges(
        NODE_INITIALIZE,
        routing.after_initialize,
        {
            NODE_LOAD_CONTEXT: NODE_LOAD_CONTEXT,
            NODE_FINALIZE: NODE_FINALIZE,
        },
    )
    g.add_conditional_edges(
        NODE_LOAD_CONTEXT,
        routing.after_load_context,
        {
            NODE_RETRIEVE: NODE_RETRIEVE,
            NODE_FINALIZE: NODE_FINALIZE,
        },
    )
    g.add_conditional_edges(
        NODE_RETRIEVE,
        routing.after_retrieve,
        {
            NODE_EXTRACT: NODE_EXTRACT,
            NODE_RETRIEVE: NODE_RETRIEVE,
            NODE_FINALIZE: NODE_FINALIZE,
        },
    )
    g.add_conditional_edges(
        NODE_EXTRACT,
        routing.after_extract,
        {
            NODE_MATCH: NODE_MATCH,
            NODE_EXTRACT: NODE_EXTRACT,
            NODE_FINALIZE: NODE_FINALIZE,
        },
    )
    g.add_conditional_edges(
        NODE_MATCH,
        routing.after_match,
        {
            NODE_COMPLIANCE: NODE_COMPLIANCE,
            NODE_MATCH: NODE_MATCH,
            NODE_FINALIZE: NODE_FINALIZE,
        },
    )
    g.add_conditional_edges(
        NODE_COMPLIANCE,
        routing.after_compliance,
        {
            NODE_DRAFT: NODE_DRAFT,
            NODE_COMPLIANCE: NODE_COMPLIANCE,
            NODE_FINALIZE: NODE_FINALIZE,
        },
    )
    g.add_conditional_edges(
        NODE_DRAFT,
        routing.after_draft,
        {
            NODE_VALIDATE: NODE_VALIDATE,
            NODE_DRAFT: NODE_DRAFT,
            NODE_FINALIZE: NODE_FINALIZE,
        },
    )
    g.add_conditional_edges(
        NODE_VALIDATE,
        routing.after_validate,
        {
            NODE_REVISE: NODE_REVISE,
            NODE_FINALIZE: NODE_FINALIZE,
        },
    )
    g.add_conditional_edges(
        NODE_REVISE,
        routing.after_revise,
        {
            NODE_VALIDATE: NODE_VALIDATE,
            NODE_FINALIZE: NODE_FINALIZE,
        },
    )
    g.add_edge(NODE_FINALIZE, END)

    return g.compile(checkpointer=checkpointer or new_memory_saver())


_COMPILED = None


def get_compiled_graph(checkpointer: MemorySaver | None = None):
    global _COMPILED
    if checkpointer is not None:
        return build_graph(checkpointer=checkpointer)
    if _COMPILED is None:
        _COMPILED = build_graph()
    return _COMPILED
