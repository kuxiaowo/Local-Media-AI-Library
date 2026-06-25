from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.config import get_settings
from app.workers import WorkerManager

settings = get_settings()
worker = WorkerManager.from_settings(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.worker_manager = worker
    worker.start()
    yield
    worker.stop()


app = FastAPI(title="Local Media AI Library", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": "Local Media AI Library API",
        "health": "/api/health",
        "frontend": "http://127.0.0.1:5173",
    }


def run_dev_server() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=True,
    )


if __name__ == "__main__":
    run_dev_server()
