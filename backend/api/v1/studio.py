"""Persistent Studio authoring API router."""

from fastapi import APIRouter

from backend.api.v1.studio_lifecycle import router as lifecycle_router
from backend.api.v1.studio_projects import router as projects_router
from backend.api.v1.studio_record_graph import router as record_graph_router
from backend.api.v1.studio_workflows import router as workflows_router


def create_studio_router() -> APIRouter:
    """Assemble persistent Studio routes before plugin route contributions."""

    router = APIRouter(tags=["studio-authoring"])
    router.include_router(projects_router)
    router.include_router(workflows_router)
    router.include_router(lifecycle_router)
    router.include_router(record_graph_router)
    return router
