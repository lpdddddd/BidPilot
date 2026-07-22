"""Agent run orchestration: start / execute / resume / retry with persistence."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.checkpoint import (
    DbCheckpointStore,
    lg_memory_is_full,
    restore_memory_saver,
    serialize_memory_saver,
)
from app.agent.graph import build_graph
from app.agent.nodes._helpers import AgentInterrupt, AgentRuntime, reset_runtime, set_runtime
from app.agent.state import GRAPH_VERSION, AgentState, empty_state
from app.models.agent import AgentEvent, AgentRun
from app.models.enums import AgentRunStatus
from app.models.project import BidProject
from app.schemas.agent_run import (
    AgentEventItem,
    AgentEventsResponse,
    AgentResultResponse,
    AgentRunListResponse,
    AgentRunRead,
    AgentRunStartRequest,
    AgentStateRead,
)
from app.services.agent_run.events import (
    EVENT_RUN_COMPLETED,
    EVENT_RUN_FAILED,
    EVENT_RUN_RESUMED,
    commit_visible,
    record_event,
    record_node_finished,
    record_node_started,
    record_tool_finished,
    record_tool_started,
    status_from_str,
)
from app.tools.agent_tools import RetrievalFn


def _now() -> datetime:
    return datetime.now(UTC)


def _safe_summary(state: AgentState) -> dict[str, Any]:
    return {
        "status": state.get("status"),
        "current_node": state.get("current_node"),
        "completed_nodes": list(state.get("completed_nodes") or []),
        "compliance_run_id": state.get("compliance_run_id"),
        "compliance_summary": state.get("compliance_summary") or {},
        "draft_ids": state.get("draft_ids") or [],
        "draft_finding_count": len(state.get("draft_findings") or []),
        "warnings": state.get("warnings") or [],
        "errors": state.get("errors") or [],
        "requirement_count": len(state.get("requirements") or []),
        "match_count": len(state.get("requirement_matches") or []),
        "citation_count": len(state.get("citations") or []),
        "critical_qualification": bool(state.get("critical_qualification")),
        "graph_version": state.get("graph_version") or GRAPH_VERSION,
    }


class AgentRunService:
    def __init__(
        self,
        db: Session,
        *,
        llm: Any | None = None,
        retrieval_fn: RetrievalFn | None = None,
    ) -> None:
        self.db = db
        self.llm = llm
        self.retrieval_fn = retrieval_fn
        self.checkpoints = DbCheckpointStore(db)

    def start_run(
        self,
        project_id: UUID,
        request: AgentRunStartRequest | None = None,
        *,
        idempotency_key: str | None = None,
        execute: bool = True,
    ) -> AgentRunRead:
        project = self.db.get(BidProject, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")

        req = request or AgentRunStartRequest()
        if idempotency_key:
            existing = self.db.scalar(
                select(AgentRun).where(
                    AgentRun.project_id == project_id,
                    AgentRun.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return self._to_read(existing)

        run = AgentRun(
            organization_id=project.organization_id,
            project_id=project.id,
            status=AgentRunStatus.pending,
            intent=req.intent or "bid_analysis_loop",
            graph_version=GRAPH_VERSION,
            idempotency_key=idempotency_key,
            input_json={
                "user_request": req.user_request,
                "requested_requirement_ids": [
                    str(x) for x in (req.requested_requirement_ids or [])
                ],
                "selected_document_ids": [str(x) for x in (req.selected_document_ids or [])],
                "metadata": req.metadata or {},
            },
            current_node=None,
        )
        self.db.add(run)
        self.db.flush()

        state = empty_state(
            run_id=run.id,
            project_id=project.id,
            organization_id=project.organization_id,
            user_request=req.user_request or "",
            requested_requirement_ids=[str(x) for x in (req.requested_requirement_ids or [])],
            selected_document_ids=[str(x) for x in (req.selected_document_ids or [])],
            metadata=req.metadata or {},
        )
        run.state_json = json.loads(json.dumps(state, default=str))
        run.started_at = _now()
        run.status = AgentRunStatus.running
        self.db.commit()
        self.db.refresh(run)

        if execute:
            return self.execute_run(run.id)
        return self._to_read(run)

    def execute_run(self, run_id: UUID) -> AgentRunRead:
        run = self.db.get(AgentRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="agent run not found")
        state: AgentState = dict(run.state_json or {})  # type: ignore[assignment]
        if not state.get("run_id"):
            state = empty_state(
                run_id=run.id,
                project_id=run.project_id,
                organization_id=run.organization_id,
            )
        return self._continue_from_state(run.id, state, memory=MemorySaver())

    def resume_run(self, run_id: UUID) -> AgentRunRead:
        run = self.db.get(AgentRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="agent run not found")
        # Idempotent: completed / blocked / cancelled runs return as-is.
        if run.status in {
            AgentRunStatus.completed,
            AgentRunStatus.completed_with_warnings,
            AgentRunStatus.blocked,
            AgentRunStatus.cancelled,
        }:
            return self._to_read(run)

        if run.status not in {
            AgentRunStatus.waiting_for_user,
            AgentRunStatus.running,
            AgentRunStatus.failed,
        }:
            return self._to_read(run)

        cp_state = self.checkpoints.load_state(str(run.id))
        # Prefer the richer of DB checkpoint vs run.state_json (seq-ordered
        # checkpoint should win; fall back to state_json when checkpoint missing).
        state: AgentState = dict(cp_state or run.state_json or {})  # type: ignore[assignment]
        if cp_state and run.state_json:
            cp_done = list(cp_state.get("completed_nodes") or [])
            run_done = list((run.state_json or {}).get("completed_nodes") or [])
            if len(run_done) > len(cp_done):
                state = dict(run.state_json)  # type: ignore[assignment]
        state.pop("lg_memory", None)  # type: ignore[arg-type]
        meta = dict(state.get("metadata") or {})
        # Clear interrupt flag so graph can continue; keep _interrupted so we don't
        # re-trigger the same interrupt_after_node.
        meta.pop("interrupt_after_node", None)
        state["metadata"] = meta
        state["interrupt_requested"] = False
        if state.get("status") == "waiting_for_user":
            state["status"] = "running"
        run.state_json = json.loads(json.dumps(state, default=str))
        run.status = AgentRunStatus.running
        record_event(
            self.db,
            agent_run_id=run.id,
            event_type=EVENT_RUN_RESUMED,
            status="running",
            node_name=state.get("current_node"),
            safe_summary="run resumed",
        )
        self.db.commit()

        # Prefer true LangGraph continue when a full MemorySaver dump exists;
        # otherwise re-stream from START with completed_nodes skip.
        lg_payload = self.checkpoints.load_lg_memory(str(run.id))
        if lg_memory_is_full(lg_payload):
            restored = restore_memory_saver(lg_payload)
            return self._continue_from_state(
                run.id,
                state,
                memory=restored,
                continue_from_checkpointer=True,
            )
        return self._continue_from_state(run.id, state, memory=MemorySaver())

    def retry_run(self, run_id: UUID) -> AgentRunRead:
        """Controlled retry on the same run_id.

        Semantics:
        - Increment ``metadata.retry_attempt`` and record ``metadata.retry_of_status``.
        - Clear error fields.
        - Remove ``current_node`` (failure point) from ``completed_nodes`` so it
          re-runs; keep earlier completed nodes so services are not duplicated.
        - Preserve compliance_run_id / draft_ids / business object IDs.
        """
        run = self.db.get(AgentRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="agent run not found")
        state: AgentState = dict(run.state_json or {})  # type: ignore[assignment]
        state.pop("lg_memory", None)  # type: ignore[arg-type]
        meta = dict(state.get("metadata") or {})
        meta["retry_attempt"] = int(meta.get("retry_attempt") or 0) + 1
        meta["retry_of_status"] = (
            run.status.value if hasattr(run.status, "value") else str(run.status)
        )
        state["metadata"] = meta
        state["errors"] = []
        state["error_code"] = None
        state["error_summary"] = None
        state["last_error_retryable"] = False
        state["status"] = "running"
        state["interrupt_requested"] = False

        failed_node = state.get("current_node") or run.current_node
        if failed_node:
            state["completed_nodes"] = [
                n for n in (state.get("completed_nodes") or []) if n != failed_node
            ]

        run.state_json = json.loads(json.dumps(state, default=str))
        run.status = AgentRunStatus.running
        run.error_code = None
        run.error_summary = None
        run.error_message = None
        self.db.commit()

        # Retry always re-injects AgentState from START with completed_nodes skip
        # (failed node removed). Fresh MemorySaver; lg_memory is observational.
        return self._continue_from_state(run.id, state, memory=MemorySaver())

    def _continue_from_state(
        self,
        run_id: UUID,
        state: AgentState,
        *,
        memory: MemorySaver | None = None,
        continue_from_checkpointer: bool = False,
    ) -> AgentRunRead:
        """Stream the graph with stable ``thread_id=str(run.id)``.

        Completed nodes short-circuit via ``completed_nodes`` so services are not
        re-invoked on resume / controlled retry.

        When ``continue_from_checkpointer`` is True and ``memory`` holds a restored
        full dump, stream ``None`` to continue from the LangGraph checkpointer
        position. On failure, fall back to streaming restored AgentState from START.
        """
        run = self.db.get(AgentRun, run_id)
        assert run is not None
        memory = memory or MemorySaver()
        graph = build_graph(checkpointer=memory)
        # Stable thread id for start, resume, and retry — never random.
        thread_id = str(run.id)
        config = {"configurable": {"thread_id": thread_id}}

        runtime = AgentRuntime(
            db=self.db,
            llm=self.llm,
            retrieval_fn=self.retrieval_fn,
            commit_fn=lambda: commit_visible(self.db),
            persist_node_start=lambda **kw: record_node_started(
                self.db,
                agent_run_id=run.id,
                **kw,
            ),
            persist_node_finish=lambda **kw: record_node_finished(
                self.db,
                agent_run_id=run.id,
                **kw,
            ),
            persist_tool_start=lambda **kw: record_tool_started(
                self.db,
                agent_run_id=run.id,
                **kw,
            ),
            persist_tool_finish=lambda **kw: record_tool_finished(
                self.db,
                agent_run_id=run.id,
                **kw,
            ),
            save_checkpoint=lambda st, node: self.checkpoints.save(
                agent_run_id=run.id,
                thread_id=thread_id,
                node_name=node,
                state=st,
                lg_memory=serialize_memory_saver(memory),
            ),
        )
        token = set_runtime(runtime)
        try:
            run.status = AgentRunStatus.running
            run.started_at = run.started_at or _now()
            self.db.flush()

            final_state = state
            stream_input: AgentState | None = state
            if continue_from_checkpointer:
                try:
                    # Merge cleaned AgentState (cleared interrupt) into the
                    # restored checkpointer so stream(None) does not re-pause.
                    graph.update_state(config, state)
                    stream_input = None
                except Exception:  # noqa: BLE001
                    # Incomplete restore — fall back to START + completed_nodes.
                    continue_from_checkpointer = False
                    stream_input = state
                    memory = MemorySaver()
                    graph = build_graph(checkpointer=memory)

            def _stream_updates(inp: AgentState | None) -> AgentState:
                out_state = final_state
                for event in graph.stream(inp, config=config, stream_mode="updates"):
                    for node_name, partial in event.items():
                        if isinstance(partial, dict):
                            out_state = {**out_state, **partial}  # type: ignore[misc]
                        self._after_node(run, node_name, out_state, memory=memory)
                        if out_state.get("interrupt_requested"):
                            raise AgentInterrupt(node_name)
                return out_state

            try:
                try:
                    final_state = _stream_updates(stream_input)
                except Exception as stream_exc:
                    # True-continue failed (empty/partial MemorySaver) — fall back.
                    if (
                        continue_from_checkpointer
                        and stream_input is None
                        and not isinstance(stream_exc, AgentInterrupt)
                    ):
                        memory = MemorySaver()
                        graph = build_graph(checkpointer=memory)
                        final_state = _stream_updates(state)
                    else:
                        raise
            except AgentInterrupt as exc:
                if runtime.last_state:
                    final_state = {**final_state, **runtime.last_state}  # type: ignore[misc]
                final_state["status"] = "waiting_for_user"
                final_state["current_node"] = exc.node
                final_state["interrupt_requested"] = True
                # Node already persisted via _after_node in the stream loop;
                # refresh status fields without a duplicate AgentStep.
                run.current_node = exc.node
                run.state_json = json.loads(json.dumps(final_state, default=str))
                run.status = AgentRunStatus.waiting_for_user
                self.checkpoints.save(
                    agent_run_id=run.id,
                    thread_id=thread_id,
                    node_name=exc.node,
                    state=dict(final_state),
                    lg_memory=serialize_memory_saver(memory),
                )
                self._persist_run(run, final_state)
                self.db.commit()
                self.db.refresh(run)
                return self._to_read(run)

            self._persist_run(run, final_state)
            self.db.commit()
            self.db.refresh(run)
            return self._to_read(run)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            state["status"] = "failed"
            state["error_code"] = "execute_error"
            state["error_summary"] = f"{type(exc).__name__}: {exc}"
            self._persist_run(run, state)
            self.db.commit()
            self.db.refresh(run)
            return self._to_read(run)
        finally:
            reset_runtime(token)

    def _after_node(
        self,
        run: AgentRun,
        node_name: str,
        state: AgentState,
        *,
        memory: MemorySaver | None = None,
    ) -> None:
        # Node opened via begin_node → persist_node_start; finish here after tools.
        # Skipped completed nodes never set runtime.current_node_name — no events.
        runtime = None
        try:
            from app.agent.nodes._helpers import get_runtime

            runtime = get_runtime()
        except RuntimeError:
            runtime = None
        started = runtime is not None and runtime.current_node_name == node_name
        if started:
            node_status = "failed" if state.get("status") == "failed" else "succeeded"
            finish_kw = {
                "node_name": node_name,
                "status": node_status,
                "agent_step_id": runtime.current_step_id,
                "output_json": {
                    "status": state.get("status"),
                    "warnings": state.get("warnings"),
                    "route_decision": state.get("route_decision"),
                    "completed_nodes": state.get("completed_nodes"),
                },
                "error_message": state.get("error_summary"),
            }
            if runtime.persist_node_finish:
                runtime.persist_node_finish(**finish_kw)
            else:
                record_node_finished(self.db, agent_run_id=run.id, **finish_kw)
        if runtime is not None:
            runtime.current_step_id = None
            runtime.current_node_name = None
        lg_memory = serialize_memory_saver(memory) if memory is not None else None
        self.checkpoints.save(
            agent_run_id=run.id,
            thread_id=str(run.id),
            node_name=node_name,
            state=dict(state),
            next_node=None,
            lg_memory=lg_memory,
        )
        run.current_node = node_name
        run.state_json = json.loads(json.dumps(state, default=str))
        run.status = status_from_str(str(state.get("status") or "running"))
        self.db.flush()
        # Mid-run visibility: independent sessions must see node_completed now.
        commit_visible(self.db)

    def _persist_run(self, run: AgentRun, state: AgentState) -> None:
        run.state_json = json.loads(json.dumps(state, default=str))
        run.current_node = state.get("current_node")
        run.graph_version = state.get("graph_version") or GRAPH_VERSION
        run.output_summary_json = _safe_summary(state)
        previous = run.status
        run.status = status_from_str(str(state.get("status") or "running"))
        run.error_code = state.get("error_code")
        run.error_summary = state.get("error_summary")
        run.error_message = state.get("error_summary")
        if run.status in {
            AgentRunStatus.completed,
            AgentRunStatus.completed_with_warnings,
            AgentRunStatus.blocked,
            AgentRunStatus.failed,
            AgentRunStatus.cancelled,
        }:
            run.finished_at = _now()
            if previous not in {
                AgentRunStatus.completed,
                AgentRunStatus.completed_with_warnings,
                AgentRunStatus.blocked,
                AgentRunStatus.failed,
                AgentRunStatus.cancelled,
            }:
                event_type = (
                    EVENT_RUN_FAILED if run.status == AgentRunStatus.failed else EVENT_RUN_COMPLETED
                )
                record_event(
                    self.db,
                    agent_run_id=run.id,
                    event_type=event_type,
                    status=(run.status.value if hasattr(run.status, "value") else str(run.status)),
                    node_name=run.current_node,
                    safe_summary=run.error_summary or f"run {event_type}",
                )
        self.db.flush()

    def get_run(self, run_id: UUID, *, project_id: UUID | None = None) -> AgentRunRead:
        run = self.db.get(AgentRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="agent run not found")
        if project_id is not None and run.project_id != project_id:
            raise HTTPException(status_code=404, detail="agent run not found")
        return self._to_read(run)

    def get_events(
        self,
        run_id: UUID,
        *,
        project_id: UUID | None = None,
        after_sequence: int | None = None,
    ) -> AgentEventsResponse:
        run = self.get_run(run_id, project_id=project_id)
        stmt = select(AgentEvent).where(AgentEvent.agent_run_id == run_id)
        if after_sequence is not None:
            stmt = stmt.where(AgentEvent.sequence > after_sequence)
        rows = list(
            self.db.scalars(
                stmt.order_by(
                    AgentEvent.sequence.asc(),
                    AgentEvent.occurred_at.asc(),
                    AgentEvent.id.asc(),
                )
            ).all()
        )
        events: list[AgentEventItem] = []
        for row in rows:
            name = row.tool_name or row.node_name or row.event_type
            attempt = row.attempt
            if attempt is None and isinstance(row.payload_json, dict):
                attempt = row.payload_json.get("attempt")
            events.append(
                AgentEventItem(
                    id=row.id,
                    event_type=row.event_type,
                    sequence=row.sequence,
                    name=name,
                    node_name=row.node_name,
                    tool_name=row.tool_name,
                    status=row.status or "ok",
                    summary=row.safe_summary,
                    safe_summary=row.safe_summary,
                    created_at=row.occurred_at or row.created_at,
                    timestamp=row.occurred_at or row.created_at,
                    duration_ms=row.duration_ms,
                    agent_step_id=row.agent_step_id,
                    tool_call_id=row.tool_call_id,
                    attempt=attempt,
                    payload={
                        "call_id": row.call_id,
                        **(row.payload_json or {}),
                    },
                )
            )
        return AgentEventsResponse(run_id=run.id, items=events, total=len(events))

    def get_result(self, run_id: UUID, *, project_id: UUID | None = None) -> AgentResultResponse:
        run = self.db.get(AgentRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="agent run not found")
        if project_id is not None and run.project_id != project_id:
            raise HTTPException(status_code=404, detail="agent run not found")
        state = dict(run.state_json or {})
        return AgentResultResponse(
            run=self._to_read(run),
            summary=run.output_summary_json or _safe_summary(state),  # type: ignore[arg-type]
            state=AgentStateRead.model_validate(state) if state else None,
            citations=list(state.get("citations") or []),
            draft_ids=[UUID(x) for x in (state.get("draft_ids") or []) if x],
            compliance_run_id=(
                UUID(state["compliance_run_id"]) if state.get("compliance_run_id") else None
            ),
            warnings=list(state.get("warnings") or []),
            errors=list(state.get("errors") or []),
        )

    def list_for_project(self, project_id: UUID, *, limit: int = 20) -> AgentRunListResponse:
        project = self.db.get(BidProject, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        rows = list(
            self.db.scalars(
                select(AgentRun)
                .where(AgentRun.project_id == project_id)
                .order_by(AgentRun.created_at.desc())
                .limit(limit)
            ).all()
        )
        return AgentRunListResponse(
            items=[self._to_read(r) for r in rows],
            total=len(rows),
        )

    def get_latest(self, project_id: UUID) -> AgentRunRead | None:
        project = self.db.get(BidProject, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        row = self.db.scalar(
            select(AgentRun)
            .where(AgentRun.project_id == project_id)
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )
        return self._to_read(row) if row else None

    def _to_read(self, run: AgentRun) -> AgentRunRead:
        stream_path = None
        if run.project_id is not None:
            stream_path = f"/api/v1/projects/{run.project_id}/agent-runs/{run.id}/events/stream"
        else:
            stream_path = f"/api/v1/agent-runs/{run.id}/events/stream"
        return AgentRunRead(
            id=run.id,
            organization_id=run.organization_id,
            project_id=run.project_id,
            status=run.status,
            intent=run.intent,
            current_node=run.current_node,
            graph_version=run.graph_version or GRAPH_VERSION,
            idempotency_key=run.idempotency_key,
            input_json=run.input_json,
            output_summary_json=run.output_summary_json,
            error_code=run.error_code,
            error_summary=run.error_summary or run.error_message,
            started_at=run.started_at,
            finished_at=run.finished_at,
            created_at=run.created_at,
            updated_at=run.updated_at,
            state=AgentStateRead.model_validate(run.state_json) if run.state_json else None,
            thread_id=str(run.id),
            events_stream_path=stream_path,
        )
