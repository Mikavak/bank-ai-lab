from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from bank_ai.api.routes.auth import router as auth_router
from bank_ai.api.routes.conversations import (
    router as conversations_router,
)
from bank_ai.api.routes.health import router as health_router
from bank_ai.api.routes.messages import router as messages_router
from bank_ai.api.routes.tickets import (
    client_router as ticket_client_router,
)
from bank_ai.api.routes.tickets import (
    operator_router as ticket_operator_router,
)
from bank_ai.api.routes.transfers import router as transfers_router
from bank_ai.infrastructure.database.session import get_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = get_engine()

    try:
        yield
    finally:
        await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Bank AI Lab",
        description="AI banking support platform",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(conversations_router)
    app.include_router(messages_router)
    app.include_router(ticket_client_router)
    app.include_router(ticket_operator_router)
    app.include_router(transfers_router)

    return app


app = create_app()
