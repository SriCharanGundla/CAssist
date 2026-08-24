from fastapi import APIRouter, Depends

from app.api.dependencies import accept_idempotency_key
from app.api.routes import (
    auth,
    document_comparisons,
    documents,
    exports,
    health,
    results,
    runs,
    uploads,
)

api_router = APIRouter(dependencies=[Depends(accept_idempotency_key)])
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, tags=["authentication"])
api_router.include_router(uploads.router, tags=["uploads"])
api_router.include_router(results.router, tags=["results"])
api_router.include_router(exports.router, tags=["exports"])
api_router.include_router(documents.router, tags=["documents"])
api_router.include_router(document_comparisons.router, tags=["documents"])
api_router.include_router(runs.router, tags=["runs"])
