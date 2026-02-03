from __future__ import annotations

import logging
from fastapi import HTTPException, status

from .config import Settings
from .providers import OpenAIProvider, GeminiProvider, AnthropicProvider

logger = logging.getLogger("api-key-server.upstream")


# Provider registry
PROVIDERS = {
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "anthropic": AnthropicProvider,
}


async def call_ai_service(product_id: str, payload: dict, settings: Settings, endpoint_type: str = "chat") -> dict:
    """
    Call AI service based on product configuration.
    Supports both legacy (single API key) and new (multi-provider) formats.

    Args:
        product_id: Product identifier
        payload: Request payload (OpenAI-compatible format)
        settings: Application settings
        endpoint_type: Type of endpoint ("chat", "image", "audio")

    Returns:
        Response from AI service (OpenAI-compatible format)
    """
    model = payload.get("model")

    # Get merged product configs (supports both separated and unified formats)
    merged_configs = settings.get_merged_product_configs()

    # Check new multi-provider format first
    if product_id in merged_configs:
        product_config = merged_configs[product_id]

        # Find provider for the requested model
        provider_info = product_config.get_provider_for_model(model)

        if not provider_info:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Model '{model}' not supported for product '{product_id}'"
            )

        provider_name, provider_config = provider_info

        if provider_name not in PROVIDERS:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail=f"Provider '{provider_name}' not implemented"
            )

        if not provider_config.api_key:
            logger.error(f"API key not configured for provider '{provider_name}' in product '{product_id}'")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"API key not configured for provider '{provider_name}'"
            )

        provider_class = PROVIDERS[provider_name]
        logger.info(f"Using provider '{provider_name}' for model '{model}' in product '{product_id}' (endpoint: {endpoint_type})")

        # Call appropriate provider method based on endpoint type
        if endpoint_type == "image":
            return await provider_class.call_image(
                api_key=provider_config.api_key,
                payload=payload,
                base_url=provider_config.base_url,
                timeout=settings.request_timeout_seconds
            )
        elif endpoint_type == "audio":
            return await provider_class.call_audio(
                api_key=provider_config.api_key,
                payload=payload,
                base_url=provider_config.base_url,
                timeout=settings.request_timeout_seconds
            )
        elif endpoint_type == "transcription":
            # Transcription requires special handling (file upload)
            return await provider_class.call_transcribe(
                api_key=provider_config.api_key,
                file_content=payload["file_content"],
                filename=payload["filename"],
                model=payload.get("model", "whisper-1"),
                language=payload.get("language"),
                prompt=payload.get("prompt"),
                response_format=payload.get("response_format", "json"),
                temperature=payload.get("temperature", 0),
                base_url=provider_config.base_url,
                timeout=settings.request_timeout_seconds
            )
        else:  # chat
            return await provider_class.call(
                api_key=provider_config.api_key,
                payload=payload,
                base_url=provider_config.base_url,
                timeout=settings.request_timeout_seconds
            )

    # Fallback to legacy format (single OpenAI API key)
    elif product_id in settings.product_keys:
        logger.info(f"Using legacy configuration for product '{product_id}'")
        api_key = settings.product_keys[product_id]

        return await OpenAIProvider.call(
            api_key=api_key,
            payload=payload,
            base_url=settings.openai_base_url,
            timeout=settings.request_timeout_seconds
        )

    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown product: {product_id}"
        )


# Legacy function name for backward compatibility
async def call_openai(product_id: str, payload: dict, settings: Settings) -> dict:
    """Legacy function name. Use call_ai_service instead."""
    return await call_ai_service(product_id, payload, settings)
