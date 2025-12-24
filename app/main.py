from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field, validator

from .auth import AuthContext, ensure_authenticated
from .config import Settings, get_settings
from .providers.gemini_image import GeminiImageProvider
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
    def validate_max_tokens(cls, value: Optional[int]) -> int:
        """max_tokensのバリデーションとデフォルト値適用"""
        # クライアントが指定しない場合はサーバーのデフォルト値を適用
        if value is None:
            return settings.max_tokens

        # 指定された場合はバリデーション
        if value < 1:
            raise ValueError("max_tokens must be >= 1")
        if value > settings.max_tokens:
            raise ValueError(
                f"max_tokens must be <= {settings.max_tokens}. "
                f"To increase this limit, set API_KEY_SERVER_MAX_TOKENS environment variable."
            )
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
    n: Optional[int] = Field(default=None, description="Number of images to generate")
    size: Optional[str] = Field(default=None, description="Image size")
    quality: Optional[str] = Field(default=None, description="Image quality (standard or hd)")
    style: Optional[str] = Field(default=None, description="Image style (vivid or natural)")
    response_format: Optional[str] = Field(default=None, description="Response format (url or b64_json)")


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
    voice: Optional[str] = Field(default=None, description="Voice to use")
    response_format: Optional[str] = Field(default=None, description="Audio format (mp3, opus, aac, flac)")
    speed: Optional[float] = Field(default=None, ge=0.25, le=4.0, description="Speed of speech")


class GeminiImageRequest(BaseModel):
    """Gemini 3 Pro Image画像生成リクエスト"""
    model: str = Field(
        default="gemini-3-pro-image-preview",
        description="モデル名"
    )
    prompt: str = Field(..., description="画像生成プロンプト")
    config: Optional[Dict[str, Any]] = Field(
        default=None,
        description="画像生成設定（resolution, aspect_ratio等）"
    )


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


@app.post("/v1/images/gemini/{product}")
async def gemini_image_generation(
    product: str,
    payload: GeminiImageRequest,
    context: AuthContext = Depends(ensure_authenticated),
    settings: Settings = Depends(get_settings),
):
    """
    Gemini 3 Pro Image画像生成エンドポイント

    Args:
        product: プロダクトID
        payload: 画像生成リクエスト
        context: 認証コンテキスト
        settings: アプリケーション設定

    Returns:
        {
            "success": bool,
            "image": {
                "format": str,
                "data": str,  # base64
                "resolution": str
            },
            "error": str  # エラー時
        }
    """
    if context.product_id != product:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Product mismatch")

    # プロダクト設定を取得
    product_config = settings.product_configs.get(product)
    if not product_config:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Product '{product}' not found")

    # Geminiプロバイダーの設定を取得
    if hasattr(product_config, 'providers'):
        # 新形式（ProductConfigクラス）
        gemini_config = product_config.providers.get("gemini")
        if not gemini_config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Gemini provider not configured for product '{product}'"
            )
        api_key = gemini_config.api_key
    else:
        # レガシー形式（OpenAIのみ）の場合はGemini未対応
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Gemini provider not configured for product '{product}'"
        )

    if not api_key:
        logger.error(f"Gemini API key not configured for product: {product}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gemini API key not configured"
        )

    await rate_limiter.check(product_id=product, user_id=context.user_id)

    logger.info(
        "Gemini Image generation request",
        extra={
            "product": product,
            "user": context.user_id,
            "method": context.method,
            "model": payload.model,
            "prompt_length": len(payload.prompt),
        },
    )

    # GeminiImageProviderを使用して画像生成
    result = await GeminiImageProvider.generate_image(
        api_key=api_key,
        prompt=payload.prompt,
        config=payload.config
    )

    if not result.get("success"):
        error_msg = result.get("error", "Unknown error")
        logger.error(f"Gemini image generation failed: {error_msg}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_msg
        )

    logger.info("Gemini image generation successful")
    return result


def create_app() -> FastAPI:
    return app
