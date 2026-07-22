from fastapi import APIRouter

from app.api.v1 import (
    ask,
    compliance,
    documents,
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

