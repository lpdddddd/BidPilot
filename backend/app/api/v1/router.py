from fastapi import APIRouter

from app.api.v1 import documents, projects, search

api_router = APIRouter()
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(documents.router, prefix="/projects", tags=["documents"])
api_router.include_router(search.router, prefix="/projects", tags=["search"])
