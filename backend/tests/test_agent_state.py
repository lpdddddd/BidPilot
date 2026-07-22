"""Unit tests for AgentState schema."""

from __future__ import annotations

from app.agent.state import GRAPH_VERSION, AgentStateModel, empty_state


def test_empty_state_has_required_fields():
    state = empty_state(run_id="r1", project_id="p1", user_request="hello")
    assert state["run_id"] == "r1"
    assert state["project_id"] == "p1"
    assert state["graph_version"] == GRAPH_VERSION
    assert state["status"] == "pending"
    assert state["retrieved_chunks"] == []
    assert state["requirements"] == []
    assert state["requirement_matches"] == []
    assert state["tool_events"] == []
    assert state["retry_counts"] == {}
    assert state["completed_nodes"] == []
    assert state["draft_findings"] == []
    model = AgentStateModel.model_validate(state)
    assert model.run_id == "r1"
    assert model.graph_version == GRAPH_VERSION
    assert model.completed_nodes == []


def test_agent_state_model_accepts_extra_routing_fields():
    state = empty_state(run_id="r2")
    state["critical_qualification"] = True
    state["draft_revise_count"] = 1
    model = AgentStateModel.model_validate(state)
    assert model.critical_qualification is True
    assert model.draft_revise_count == 1
