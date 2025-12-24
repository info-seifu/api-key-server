"""Anthropic Claude API provider adapter."""

from __future__ import annotations

import logging
import httpx
from fastapi import HTTPException, status

logger = logging.getLogger("api-key-server.providers.anthropic")


class AnthropicProvider:
    """Adapter for Anthropic Claude API."""

    DEFAULT_BASE_URL = "https://api.anthropic.com/v1/messages"
    ANTHROPIC_VERSION = "2023-06-01"

    @staticmethod
    async def call(api_key: str, payload: dict, base_url: str | None = None, timeout: int = 30) -> dict:
        """
        Call Anthropic Claude API and convert to OpenAI-compatible format.

        Args:
            api_key: Anthropic API key
            payload: Request payload (OpenAI format)
            base_url: Custom endpoint URL (optional)
            timeout: Request timeout in seconds

        Returns:
            Response in OpenAI-compatible format
        """
        url = base_url or AnthropicProvider.DEFAULT_BASE_URL

        headers = {
            "x-api-key": api_key,
            "anthropic-version": AnthropicProvider.ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }

        # Convert OpenAI format to Anthropic format
        anthropic_payload = AnthropicProvider._convert_request(payload)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, headers=headers, json=anthropic_payload)
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Anthropic API request failed: {str(exc)}"
            )

        if response.status_code >= 500:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Anthropic service error"
            )
        if response.status_code == 401:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Anthropic authentication failed"
            )
        if response.status_code >= 400:
            # 内部ログには詳細を記録、クライアントには一般的なメッセージ
            logger.warning(f"Anthropic API error: {response.status_code} - {response.text}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request parameters"
            )

        # Convert Anthropic response to OpenAI format
        anthropic_response = response.json()
        return AnthropicProvider._convert_response(anthropic_response, payload.get("model", "claude-3-5-sonnet"))

    @staticmethod
    def _convert_request(openai_payload: dict) -> dict:
        """Convert OpenAI request format to Anthropic format."""
        messages = openai_payload.get("messages", [])

        # Extract system message if present
        system_message = None
        claude_messages = []

        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                claude_messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })

        # max_tokensの取得（設定ファイルのデフォルト値を使用）
        from app.config import get_settings

        config = get_settings()

        anthropic_payload = {
            "model": openai_payload.get("model", "claude-3-5-sonnet-20241022"),
            "messages": claude_messages,
            "max_tokens": openai_payload.get("max_tokens", config.max_tokens)  # リクエストになければ設定値を使用
        }

        if system_message:
            anthropic_payload["system"] = system_message

        if "temperature" in openai_payload:
            anthropic_payload["temperature"] = openai_payload["temperature"]

        return anthropic_payload

    @staticmethod
    def _convert_response(anthropic_response: dict, model: str) -> dict:
        """Convert Anthropic response format to OpenAI format."""
        # Extract content from Anthropic response
        content_blocks = anthropic_response.get("content", [])

        if not content_blocks:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="No content in Anthropic response"
            )

        # Combine all text blocks
        text = "".join(block.get("text", "") for block in content_blocks if block.get("type") == "text")

        # Build OpenAI-compatible response
        openai_response = {
            "id": anthropic_response.get("id", "anthropic-unknown"),
            "object": "chat.completion",
            "created": 0,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": text
                    },
                    "finish_reason": anthropic_response.get("stop_reason", "stop")
                }
            ],
            "usage": {
                "prompt_tokens": anthropic_response.get("usage", {}).get("input_tokens", 0),
                "completion_tokens": anthropic_response.get("usage", {}).get("output_tokens", 0),
                "total_tokens": (
                    anthropic_response.get("usage", {}).get("input_tokens", 0) +
                    anthropic_response.get("usage", {}).get("output_tokens", 0)
                )
            }
        }

        return openai_response

    @staticmethod
    async def call_image(api_key: str, payload: dict, base_url: str | None = None, timeout: int = 30) -> dict:
        """Anthropic does not support image generation."""
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Anthropic does not support image generation"
        )

    @staticmethod
    async def call_audio(api_key: str, payload: dict, base_url: str | None = None, timeout: int = 30):
        """Anthropic does not support audio generation."""
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Anthropic does not support audio generation"
        )
