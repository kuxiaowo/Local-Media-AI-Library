from fastapi import APIRouter

from app.config import get_settings
from app.models.schemas import OllamaModelsResponse, OllamaStatusResponse
from app.services.ollama_client import OllamaClient

router = APIRouter(prefix="/models", tags=["models"])


@router.get("/status", response_model=OllamaStatusResponse)
async def model_status() -> OllamaStatusResponse:
    settings = get_settings()
    ok, error = await OllamaClient(settings).health_check()
    return OllamaStatusResponse(ok=ok, base_url=settings.ollama_base_url, error=error)


@router.get("/ollama", response_model=OllamaModelsResponse)
async def ollama_models() -> OllamaModelsResponse:
    models = await OllamaClient().list_models()
    return OllamaModelsResponse(models=models)
