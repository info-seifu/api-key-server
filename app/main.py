from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field, validator

from .auth import AuthContext, ensure_authenticated, verify_hmac_multipart
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

    # 使用量ログを記録
    usage = response.get("usage", {}) if isinstance(response, dict) else {}
    logger.info(
        "request completed",
        extra={
            "product": product,
            "user": context.user_id,
            "model": payload.model,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
    )

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


# 音声ファイルサイズ制限（25MB - OpenAI Whisper と同じ）
MAX_AUDIO_FILE_SIZE = 25 * 1024 * 1024

# 許可される音声フォーマット
ALLOWED_AUDIO_FORMATS = {
    "audio/mpeg", "audio/mp3", "audio/mp4", "audio/m4a",
    "audio/wav", "audio/x-wav", "audio/webm", "audio/ogg",
    "video/mp4", "video/webm",  # 動画ファイルも音声抽出可能
}


@app.post("/v1/audio/transcriptions/{product}")
async def audio_transcriptions(
    product: str,
    file: UploadFile = File(..., description="Audio file to transcribe"),
    model: str = Form(default="whisper-1", description="Model to use"),
    language: Optional[str] = Form(default=None, description="ISO-639-1 language code"),
    prompt: Optional[str] = Form(default=None, description="Optional text to guide transcription"),
    response_format: Optional[str] = Form(default="json", description="Response format"),
    temperature: Optional[float] = Form(default=0, description="Sampling temperature"),
    x_timestamp: Optional[str] = Header(default=None, alias="X-Timestamp"),
    x_signature: Optional[str] = Header(default=None, alias="X-Signature"),
    x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id"),
    settings: Settings = Depends(get_settings),
):
    """
    Audio transcription endpoint (OpenAI Whisper compatible).

    Transcribes audio file to text using OpenAI Whisper API.
    Supports HMAC authentication for multipart/form-data requests.
    """
    # HMAC authentication (multipart 専用)
    if not (x_timestamp and x_signature and x_client_id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="HMAC authentication required (X-Timestamp, X-Signature, X-Client-Id)"
        )

    # Build form fields for HMAC verification (file を除く)
    form_fields = {"model": model}
    if language:
        form_fields["language"] = language
    if prompt:
        form_fields["prompt"] = prompt
    if response_format:
        form_fields["response_format"] = response_format
    if temperature is not None:
        form_fields["temperature"] = temperature

    context = await verify_hmac_multipart(
        client_id=x_client_id,
        timestamp=x_timestamp,
        signature=x_signature,
        product_id=product,
        method="POST",
        path=f"/v1/audio/transcriptions/{product}",
        form_fields=form_fields,
        settings=settings,
    )

    if context.product_id != product:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Product mismatch")

    # Validate model is allowed for this product
    allowed_models = settings.get_allowed_models_for_product(product)
    if not allowed_models:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Product '{product}' not found")
    if model not in allowed_models:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Model '{model}' is not allowed for product '{product}'. Allowed models: {allowed_models}"
        )

    # Rate limit check
    await rate_limiter.check(product_id=product, user_id=context.user_id)

    # Read and validate file
    file_content = await file.read()

    if len(file_content) > MAX_AUDIO_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File size exceeds limit ({MAX_AUDIO_FILE_SIZE // (1024 * 1024)}MB)"
        )

    if len(file_content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty file"
        )

    logger.info(
        "proxying audio transcription request",
        extra={
            "product": product,
            "user": context.user_id,
            "method": context.method,
            "model": model,
            "file_size": len(file_content),
            "audio_filename": file.filename,
        },
    )

    # Build payload for upstream
    payload = {
        "file_content": file_content,
        "filename": file.filename or "audio.webm",
        "model": model,
        "language": language,
        "prompt": prompt,
        "response_format": response_format or "json",
        "temperature": temperature or 0,
    }

    response = await call_ai_service(
        product_id=product,
        payload=payload,
        settings=settings,
        endpoint_type="transcription"
    )

    # 使用量ログを記録（Whisper APIはトークン情報を返さないが、将来の拡張に備えて）
    usage = response.get("usage", {}) if isinstance(response, dict) else {}
    logger.info(
        "transcription completed",
        extra={
            "product": product,
            "user": context.user_id,
            "model": model,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "file_size": len(file_content),
        },
    )

    return response


def create_app() -> FastAPI:
    return app
