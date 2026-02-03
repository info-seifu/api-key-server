"""OpenAI API provider adapter."""

from __future__ import annotations

import logging
import httpx
from fastapi import HTTPException, status

logger = logging.getLogger("api-key-server.providers.openai")


class OpenAIProvider:
    """Adapter for OpenAI API."""

    DEFAULT_BASE_URL = "https://api.openai.com/v1/chat/completions"

    @staticmethod
    async def call(api_key: str, payload: dict, base_url: str | None = None, timeout: int = 30) -> dict:
        """
        Call OpenAI chat completions API.

        Args:
            api_key: OpenAI API key
            payload: Request payload (OpenAI format)
            base_url: Custom endpoint URL (optional)
            timeout: Request timeout in seconds

        Returns:
            Response from OpenAI API
        """
        url = base_url or OpenAIProvider.DEFAULT_BASE_URL

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.RequestError as exc:
            logger.error(f"OpenAI API request failed: {exc}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Upstream service unavailable"
            )

        if response.status_code >= 500:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="OpenAI service error"
            )
        if response.status_code == 401:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="OpenAI authentication failed"
            )
        if response.status_code >= 400:
            # 内部ログには詳細を記録、クライアントには一般的なメッセージ
            logger.warning(f"OpenAI API error: {response.status_code} - {response.text}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request parameters"
            )

        return response.json()

    @staticmethod
    async def call_image(api_key: str, payload: dict, base_url: str | None = None, timeout: int = 30) -> dict:
        """
        Call OpenAI image generation API.

        Args:
            api_key: OpenAI API key
            payload: Request payload (OpenAI format)
            base_url: Custom endpoint URL (optional)
            timeout: Request timeout in seconds

        Returns:
            Response from OpenAI API
        """
        # Use images/generations endpoint
        url = "https://api.openai.com/v1/images/generations"
        if base_url and "images/generations" not in base_url:
            # If custom base_url is provided but doesn't include the path, append it
            url = base_url.rstrip("/") + "/images/generations"
        elif base_url:
            url = base_url

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.RequestError as exc:
            logger.error(f"OpenAI image API request failed: {exc}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Upstream service unavailable"
            )

        if response.status_code >= 500:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="OpenAI service error"
            )
        if response.status_code == 401:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="OpenAI authentication failed"
            )
        if response.status_code >= 400:
            # 内部ログには詳細を記録、クライアントには一般的なメッセージ
            logger.warning(f"OpenAI image API error: {response.status_code} - {response.text}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request parameters"
            )

        return response.json()

    @staticmethod
    async def call_audio(api_key: str, payload: dict, base_url: str | None = None, timeout: int = 30):
        """
        Call OpenAI audio speech API.

        Args:
            api_key: OpenAI API key
            payload: Request payload (OpenAI format)
            base_url: Custom endpoint URL (optional)
            timeout: Request timeout in seconds

        Returns:
            Response from OpenAI API (binary audio data)
        """
        from fastapi.responses import Response

        # Use audio/speech endpoint
        url = "https://api.openai.com/v1/audio/speech"
        if base_url and "audio/speech" not in base_url:
            url = base_url.rstrip("/") + "/audio/speech"
        elif base_url:
            url = base_url

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.RequestError as exc:
            logger.error(f"OpenAI audio API request failed: {exc}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Upstream service unavailable"
            )

        if response.status_code >= 500:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="OpenAI service error"
            )
        if response.status_code == 401:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="OpenAI authentication failed"
            )
        if response.status_code >= 400:
            # 内部ログには詳細を記録、クライアントには一般的なメッセージ
            logger.warning(f"OpenAI audio API error: {response.status_code} - {response.text}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request parameters"
            )

        # For audio, return binary content with proper content type
        content_type = response.headers.get("content-type", "audio/mpeg")
        return Response(content=response.content, media_type=content_type)

    @staticmethod
    async def call_transcribe(
        api_key: str,
        file_content: bytes,
        filename: str,
        model: str = "whisper-1",
        language: str | None = None,
        prompt: str | None = None,
        response_format: str = "json",
        temperature: float = 0,
        base_url: str | None = None,
        timeout: int = 120
    ) -> dict:
        """
        Call OpenAI audio transcription API (Whisper).

        Args:
            api_key: OpenAI API key
            file_content: Audio file content (bytes)
            filename: Original filename
            model: Model to use (whisper-1)
            language: ISO-639-1 language code (optional)
            prompt: Optional text to guide transcription
            response_format: Response format (json, text, verbose_json)
            temperature: Sampling temperature (0-1)
            base_url: Custom endpoint URL (optional)
            timeout: Request timeout in seconds

        Returns:
            Transcription result (OpenAI format)
        """
        url = "https://api.openai.com/v1/audio/transcriptions"
        if base_url and "audio/transcriptions" not in base_url:
            url = base_url.rstrip("/") + "/audio/transcriptions"
        elif base_url:
            url = base_url

        headers = {
            "Authorization": f"Bearer {api_key}",
        }

        # Build multipart form data
        files = {
            "file": (filename, file_content),
        }
        data = {
            "model": model,
            "response_format": response_format,
            "temperature": str(temperature),
        }
        if language:
            data["language"] = language
        if prompt:
            data["prompt"] = prompt

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, headers=headers, files=files, data=data)
        except httpx.RequestError as exc:
            logger.error(f"OpenAI transcription API request failed: {exc}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Upstream service unavailable"
            )

        if response.status_code >= 500:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="OpenAI service error"
            )
        if response.status_code == 401:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="OpenAI authentication failed"
            )
        if response.status_code >= 400:
            logger.warning(f"OpenAI transcription API error: {response.status_code} - {response.text}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request parameters"
            )

        # response_format が "text" の場合は {"text": ...} 形式に変換
        if response_format == "text":
            return {"text": response.text}

        return response.json()
