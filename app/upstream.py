from __future__ import annotations

import httpx
from fastapi import HTTPException, status

from .config import Settings


async def call_openai(product_id: str, payload: dict, settings: Settings) -> dict:
    if product_id not in settings.product_keys:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown product")

    api_key = settings.product_keys[product_id]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            response = await client.post(settings.openai_base_url, headers=headers, json=payload)
    except httpx.RequestError as exc:  # pragma: no cover
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    if response.status_code >= 500:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Upstream service error")
    if response.status_code == 401:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Upstream authentication failed")
    if response.status_code >= 400:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=response.text)

    return response.json()
