from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field, validator

from .auth import AuthContext, ensure_authenticated
from .config import Settings, get_settings
from .rate_limit import RateLimiter
from .upstream import call_openai, call_ai_service

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

    # Validate model is allowed for this product
    allowed_models = settings.get_allowed_models_for_product(product)
    if not allowed_models:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Product '{product}' not found")
    if payload.model not in allowed_models:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Model '{payload.model}' is not allowed for product '{product}'. Allowed models: {allowed_models}"
        )

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


class ImageGenerationRequest(BaseModel):
    model: str
    prompt: str
    n: Optional[int] = Field(default=1, description="Number of images to generate")
    size: Optional[str] = Field(default="1024x1024", description="Image size")
    quality: Optional[str] = Field(default="standard", description="Image quality (standard or hd)")
    style: Optional[str] = Field(default="vivid", description="Image style (vivid or natural)")
    response_format: Optional[str] = Field(default="url", description="Response format (url or b64_json)")


@app.post("/v1/images/generations/{product}")
async def image_generations(
    product: str,
    payload: ImageGenerationRequest,
    context: AuthContext = Depends(ensure_authenticated),
    settings: Settings = Depends(get_settings),
):
    """Image generation endpoint (OpenAI-compatible)."""
    if context.product_id != product:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Product mismatch")

    # Validate model is allowed for this product
    allowed_models = settings.get_allowed_models_for_product(product)
    if not allowed_models:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Product '{product}' not found")
    if payload.model not in allowed_models:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Model '{payload.model}' is not allowed for product '{product}'. Allowed models: {allowed_models}"
        )

    await rate_limiter.check(product_id=product, user_id=context.user_id)

    request_body = payload.model_dump(exclude_none=True)
    logger.info(
        "proxying image generation request",
        extra={
            "product": product,
            "user": context.user_id,
            "method": context.method,
            "model": payload.model,
        },
    )

    response = await call_ai_service(product_id=product, payload=request_body, settings=settings, endpoint_type="image")
    return response


class AudioSpeechRequest(BaseModel):
    model: str
    input: str = Field(description="Text to convert to speech")
    voice: Optional[str] = Field(default="alloy", description="Voice to use")
    response_format: Optional[str] = Field(default="mp3", description="Audio format (mp3, opus, aac, flac)")
    speed: Optional[float] = Field(default=1.0, ge=0.25, le=4.0, description="Speed of speech")


@app.post("/v1/audio/speech/{product}")
async def audio_speech(
    product: str,
    payload: AudioSpeechRequest,
    context: AuthContext = Depends(ensure_authenticated),
    settings: Settings = Depends(get_settings),
):
    """Audio speech generation endpoint (OpenAI-compatible)."""
    if context.product_id != product:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Product mismatch")

    # Validate model is allowed for this product
    allowed_models = settings.get_allowed_models_for_product(product)
    if not allowed_models:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Product '{product}' not found")
    if payload.model not in allowed_models:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Model '{payload.model}' is not allowed for product '{product}'. Allowed models: {allowed_models}"
        )

    await rate_limiter.check(product_id=product, user_id=context.user_id)

    request_body = payload.model_dump(exclude_none=True)
    logger.info(
        "proxying audio speech request",
        extra={
            "product": product,
            "user": context.user_id,
            "method": context.method,
            "model": payload.model,
        },
    )

    response = await call_ai_service(product_id=product, payload=request_body, settings=settings, endpoint_type="audio")
    return response


def create_app() -> FastAPI:
    return app
