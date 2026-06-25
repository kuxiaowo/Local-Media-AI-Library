from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.schemas import SearchRequest, SearchResponse
from app.services.ollama_client import OllamaClient
from app.services.search_service import search_media

router = APIRouter(prefix="/search", tags=["search"])


@router.post("", response_model=SearchResponse)
async def search(payload: SearchRequest, db: Session = Depends(get_db)) -> SearchResponse:
    return await search_media(db, payload, OllamaClient())
