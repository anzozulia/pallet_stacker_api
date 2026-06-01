"""FastAPI application factory. The API tier is stateless; all shared state
(queue, job status, results) lives in Redis via an arq pool."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI

from pallet_api.config import settings
from pallet_api.api.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        yield
    finally:
        await app.state.redis.aclose()


def create_app() -> FastAPI:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = FastAPI(title="pallet-packer-api", version=settings.version,
                  lifespan=lifespan)
    app.include_router(router, prefix="/api/v1")
    return app


app = create_app()
