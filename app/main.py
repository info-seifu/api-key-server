from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field, validator

from .auth import AuthContext, ensure_authenticated
from .config import Settings, get_settings
from .rate_limit import RateLimiter
from .upstream import call_openai

logger = logging.getLogger("api-key-server")
logging.basicConfig(level=logging.INFO)

settings = get_settings()
rate_limiter = RateLimiter(settings)
app = FastAPI(title=settings.app_name)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    max_tokens: Optional[int] = Field(default=None, description="Upper bound for generated tokens")
    temperature: Optional[float] = Field(default=None, description="Sampling temperature")
    stream: bool = Field(default=False, description="Enable streaming response")

    @validator("model")
    def validate_model(cls, value: str) -> str:
        allowed = settings.allowed_models
        if value not in allowed:
            raise ValueError(f"model must be one of {allowed}")
        return value

    @validator("max_tokens")
    def validate_max_tokens(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value > settings.max_tokens:
            raise ValueError(f"max_tokens must be <= {settings.max_tokens}")
        return value

    @validator("temperature")
    def validate_temperature(cls, value: Optional[float]) -> Optional[float]:
        if value is None:
            return value
        if not (settings.min_temperature <= value <= settings.max_temperature):
            raise ValueError(
                f"temperature must be between {settings.min_temperature} and {settings.max_temperature}"
            )
        return value


@app.middleware("http")
async def mask_sensitive_fields(request: Request, call_next):  # type: ignore[override]
    response = await call_next(request)
    response.headers["X-App"] = settings.app_name
    return response


@app.get("/healthz")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/v1/chat/{product}")
async def chat_completions(
    product: str,
    payload: ChatRequest,
    context: AuthContext = Depends(ensure_authenticated),
    settings: Settings = Depends(get_settings),
):
    if context.product_id != product:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Product mismatch")

    await rate_limiter.check(product_id=product, user_id=context.user_id)

    if payload.stream:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Streaming is not supported in this deployment")

    request_body = payload.model_dump(exclude_none=True)
    logger.info(
        "proxying request",
        extra={
            "product": product,
            "user": context.user_id,
            "method": context.method,
            "model": payload.model,
        },
    )

    response = await call_openai(product_id=product, payload=request_body, settings=settings)
    return response


def create_app() -> FastAPI:
    return app
