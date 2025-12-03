"""OpenAI API provider adapter."""

from __future__ import annotations

import httpx
from fastapi import HTTPException, status


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
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"OpenAI API request failed: {str(exc)}"
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
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=response.text
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
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"OpenAI API request failed: {str(exc)}"
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
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=response.text
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
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"OpenAI API request failed: {str(exc)}"
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
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=response.text
            )

        # For audio, return binary content with proper content type
        content_type = response.headers.get("content-type", "audio/mpeg")
        return Response(content=response.content, media_type=content_type)
