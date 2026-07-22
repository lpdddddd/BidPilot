from fastapi import APIRouter

from app.api.v1 import (
    agent_runs,
    ask,
    compliance,
    documents,
    evaluation,
    matches,
    projects,
    proposal_drafts,
    requirements,
    search,
)

api_router = APIRouter()
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(documents.router, prefix="/projects", tags=["documents"])
api_router.include_router(search.router, prefix="/projects", tags=["search"])
api_router.include_router(ask.router, prefix="/projects", tags=["ask"])
api_router.include_router(requirements.router, prefix="/projects", tags=["requirements"])
api_router.include_router(matches.router, prefix="/projects", tags=["matches"])
api_router.include_router(
    proposal_drafts.router, prefix="/projects", tags=["proposal-drafts"]
)
api_router.include_router(compliance.router, prefix="/projects", tags=["compliance"])
api_router.include_router(evaluation.router, prefix="/projects", tags=["evaluation"])
api_router.include_router(
    agent_runs.project_router, prefix="/projects", tags=["agent-runs"]
)
api_router.include_router(agent_runs.run_router, prefix="/agent-runs", tags=["agent-runs"])

