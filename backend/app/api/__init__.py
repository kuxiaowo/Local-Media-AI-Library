from fastapi import APIRouter

from app.api.routes_directory_rules import router as directory_rules_router
from app.api.routes_health import router as health_router
from app.api.routes_jobs import router as jobs_router
from app.api.routes_media import router as media_router
from app.api.routes_models import router as models_router
from app.api.routes_scan import router as scan_router
from app.api.routes_search import router as search_router
from app.api.routes_settings import router as settings_router

api_router = APIRouter(prefix="/api")
api_router.include_router(health_router)
api_router.include_router(models_router)
api_router.include_router(directory_rules_router)
api_router.include_router(scan_router)
api_router.include_router(jobs_router)
api_router.include_router(media_router)
api_router.include_router(search_router)
api_router.include_router(settings_router)
