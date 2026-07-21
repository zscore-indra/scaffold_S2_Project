"""Application entrypoint. Run with: python -m uvicorn app.main:app --reload"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI  # noqa: E402  (load env before importing app modules)

from app.db import init_db  # noqa: E402
from app.routes import router  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create database tables on startup."""
    init_db()
    yield


app = FastAPI(title="ExpenseFlow API", version="0.1.0", lifespan=lifespan)
app.include_router(router)
