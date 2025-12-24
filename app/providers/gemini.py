"""Google Gemini API provider adapter."""

from __future__ import annotations

import logging
import httpx
from fastapi import HTTPException, status

logger = logging.getLogger("api-key-server.providers.gemini")


class GeminiProvider:
    """Adapter for Google Gemini API."""

    DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    @staticmethod
    async def call(api_key: str, payload: dict, base_url: str | None = None, timeout: int = 30) -> dict:
        """
        Call Google Gemini API and convert to OpenAI-compatible format.

        Args:
            api_key: Google API key
            payload: Request payload (OpenAI format)
            base_url: Custom endpoint URL (optional)
            timeout: Request timeout in seconds

        Returns:
            Response in OpenAI-compatible format
        """
        model = payload.get("model", "gemini-1.5-pro")

        # Build URL
        if base_url:
            url = base_url
        else:
            url = GeminiProvider.DEFAULT_BASE_URL.format(model=model)

        url = f"{url}?key={api_key}"

        # Convert OpenAI format to Gemini format
        gemini_payload = GeminiProvider._convert_request(payload)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=gemini_payload)
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Gemini API request failed: {str(exc)}"
            )

        if response.status_code >= 500:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Gemini service error"
            )
        if response.status_code == 401 or response.status_code == 403:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Gemini authentication failed"
            )
        if response.status_code >= 400:
            # 内部ログには詳細を記録、クライアントには一般的なメッセージ
            logger.warning(f"Gemini API error: {response.status_code} - {response.text}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request parameters"
            )

        # Convert Gemini response to OpenAI format
        gemini_response = response.json()
        return GeminiProvider._convert_response(gemini_response, model)

    @staticmethod
    def _convert_request(openai_payload: dict) -> dict:
        """Convert OpenAI request format to Gemini format."""
        messages = openai_payload.get("messages", [])

        # Convert messages to Gemini contents format
        contents = []
        for msg in messages:
            role = "user" if msg["role"] in ["user", "system"] else "model"
            contents.append({
                "role": role,
                "parts": [{"text": msg["content"]}]
            })

        gemini_payload = {"contents": contents}

        # Add generation config if specified
        generation_config = {}
        if "temperature" in openai_payload:
            generation_config["temperature"] = openai_payload["temperature"]

        # max_tokensの取得（設定ファイルのデフォルト値を使用）
        from app.config import get_settings
        config = get_settings()
        generation_config["maxOutputTokens"] = openai_payload.get("max_tokens", config.max_tokens)

        if generation_config:
            gemini_payload["generationConfig"] = generation_config

        return gemini_payload

    @staticmethod
    def _convert_response(gemini_response: dict, model: str) -> dict:
        """Convert Gemini response format to OpenAI format."""
        # Extract the text from Gemini response
        candidates = gemini_response.get("candidates", [])
        if not candidates:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="No candidates in Gemini response"
            )

        first_candidate = candidates[0]
        content_parts = first_candidate.get("content", {}).get("parts", [])

        if not content_parts:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="No content in Gemini response"
            )

        text = content_parts[0].get("text", "")

        # Build OpenAI-compatible response
        openai_response = {
            "id": "gemini-" + str(hash(text))[:8],
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
                    "finish_reason": "stop"
                }
            ],
            "usage": {
                "prompt_tokens": gemini_response.get("usageMetadata", {}).get("promptTokenCount", 0),
                "completion_tokens": gemini_response.get("usageMetadata", {}).get("candidatesTokenCount", 0),
                "total_tokens": gemini_response.get("usageMetadata", {}).get("totalTokenCount", 0)
            }
        }

        return openai_response

    @staticmethod
    async def call_image(api_key: str, payload: dict, base_url: str | None = None, timeout: int = 30) -> dict:
        """
        Call Google Gemini 3 Pro Image generation API.

        Args:
            api_key: Google API key
            payload: Request payload (OpenAI format)
            base_url: Custom endpoint URL (optional)
            timeout: Request timeout in seconds

        Returns:
            Response in OpenAI-compatible format
        """
        model = payload.get("model", "gemini-3-pro-image-preview")
        prompt = payload.get("prompt", "")
        size = payload.get("size", "1024x1024")

        # Build URL - Gemini 3 Pro Image uses generateContent endpoint
        if base_url:
            url = base_url
        else:
            # Use Gemini 3 Pro Image API endpoint
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

        url = f"{url}?key={api_key}"

        # Map OpenAI size to Gemini imageSize
        # OpenAI: "1024x1024", "2048x2048", "4096x4096"
        # Gemini: "1K", "2K", "4K"
        size_map = {
            "1024x1024": "1K",
            "2048x2048": "2K",
            "4096x4096": "4K"
        }
        image_size = size_map.get(size, "2K")

        # Determine aspect ratio from size
        # For square images, use 1:1
        aspect_ratio = "1:1"

        # Convert OpenAI format to Gemini 3 Pro Image format
        gemini_payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": {
                    "aspectRatio": aspect_ratio,
                    "imageSize": image_size
                }
            }
        }

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=gemini_payload)
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Gemini API request failed: {str(exc)}"
            )

        if response.status_code >= 500:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Gemini service error"
            )
        if response.status_code == 401 or response.status_code == 403:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Gemini authentication failed"
            )
        if response.status_code >= 400:
            # 内部ログには詳細を記録、クライアントには一般的なメッセージ
            logger.warning(f"Gemini image API error: {response.status_code} - {response.text}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request parameters"
            )

        # Convert Gemini response to OpenAI format
        gemini_response = response.json()

        # Gemini 3 Pro Image returns candidates with inlineData
        candidates = gemini_response.get("candidates", [])
        if not candidates:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="No candidates in Gemini response"
            )

        # Build OpenAI-compatible response
        openai_response = {
            "created": 0,
            "data": []
        }

        # Extract images from candidates
        for candidate in candidates:
            content = candidate.get("content", {})
            parts = content.get("parts", [])

            for part in parts:
                if "inlineData" in part:
                    inline_data = part["inlineData"]
                    mime_type = inline_data.get("mimeType", "")

                    # Only process image data
                    if mime_type.startswith("image/"):
                        image_data = inline_data.get("data", "")
                        openai_response["data"].append({
                            "b64_json": image_data if payload.get("response_format") == "b64_json" else None,
                            "url": f"data:{mime_type};base64,{image_data}" if payload.get("response_format") != "b64_json" else None
                        })

        return openai_response

    @staticmethod
    async def call_audio(api_key: str, payload: dict, base_url: str | None = None, timeout: int = 30):
        """
        Call Google Gemini audio generation (TTS) API.

        Args:
            api_key: Google API key
            payload: Request payload (OpenAI format)
            base_url: Custom endpoint URL (optional)
            timeout: Request timeout in seconds

        Returns:
            Response with audio data
        """
        from fastapi.responses import Response

        model = payload.get("model", "gemini-2.5-pro-preview-tts")
        text_input = payload.get("input", "")

        # Build URL
        if base_url:
            url = base_url
        else:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

        url = f"{url}?key={api_key}"

        # Build Gemini payload for audio generation
        gemini_payload = {
            "contents": [{
                "parts": [{
                    "text": text_input
                }]
            }],
            "generationConfig": {
                "responseModalities": ["AUDIO"]
            }
        }

        # Add voice/speed if supported
        if "voice" in payload or "speed" in payload:
            speech_config = {}
            if "voice" in payload:
                speech_config["voiceConfig"] = {"prebuiltVoiceConfig": {"voiceName": payload["voice"]}}
            gemini_payload["generationConfig"]["speechConfig"] = speech_config

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=gemini_payload)
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Gemini API request failed: {str(exc)}"
            )

        if response.status_code >= 500:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Gemini service error"
            )
        if response.status_code == 401 or response.status_code == 403:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Gemini authentication failed"
            )
        if response.status_code >= 400:
            # 内部ログには詳細を記録、クライアントには一般的なメッセージ
            logger.warning(f"Gemini audio API error: {response.status_code} - {response.text}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request parameters"
            )

        # Extract audio from Gemini response
        gemini_response = response.json()
        candidates = gemini_response.get("candidates", [])
        if not candidates:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="No candidates in Gemini response"
            )

        # Get inline audio data
        content = candidates[0].get("content", {})
        parts = content.get("parts", [])

        # Find audio part
        audio_data = None
        for part in parts:
            if "inlineData" in part:
                inline_data = part["inlineData"]
                if "audio/" in inline_data.get("mimeType", ""):
                    audio_data = inline_data.get("data")
                    break

        if not audio_data:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="No audio data in Gemini response"
            )

        # Convert base64 to binary
        import base64
        audio_bytes = base64.b64decode(audio_data)

        # Return audio response
        response_format = payload.get("response_format", "mp3")
        content_type_map = {
            "mp3": "audio/mpeg",
            "opus": "audio/opus",
            "aac": "audio/aac",
            "flac": "audio/flac"
        }
        content_type = content_type_map.get(response_format, "audio/mpeg")

        return Response(content=audio_bytes, media_type=content_type)
