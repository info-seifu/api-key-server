"""Google Gemini API provider adapter."""

from __future__ import annotations

import httpx
from fastapi import HTTPException, status


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
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=response.text
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
        if "max_tokens" in openai_payload:
            generation_config["maxOutputTokens"] = openai_payload["max_tokens"]

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
