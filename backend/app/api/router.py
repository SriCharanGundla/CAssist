from fastapi import APIRouter

from app.api.routes import auth, documents, exports, health, results, uploads

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, tags=["authentication"])
api_router.include_router(uploads.router, tags=["uploads"])
api_router.include_router(results.router, tags=["results"])
api_router.include_router(exports.router, tags=["exports"])
api_router.include_router(documents.router, tags=["documents"])
