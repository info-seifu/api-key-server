"""
gemini_image.py

Gemini 3 Pro Image (gemini-3-pro-image-preview) 専用プロバイダー
generateContentメソッドを使用して画像生成を行う
"""
import logging
import httpx
from typing import Dict, Any, Optional

logger = logging.getLogger("api-key-server.providers.gemini_image")


class GeminiImageProvider:
    """
    Gemini 3 Pro Image専用プロバイダー

    generateContent APIを使用し、responseModalities: ["TEXT", "IMAGE"]を指定
    """

    # Gemini API エンドポイント
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    DEFAULT_MODEL = "gemini-3-pro-image-preview"

    # 解像度マッピング（1K/2K/4K → ピクセル数）
    RESOLUTION_MAP = {
        "1K": "1024x1024",
        "2K": "2048x2048",
        "4K": "4096x4096"
    }

    @classmethod
    async def generate_image(
        cls,
        api_key: str,
        prompt: str,
        config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        画像生成メインメソッド

        Args:
            api_key: Gemini API Key
            prompt: 画像生成プロンプト
            config: 画像生成設定
                {
                    "resolution": "1K" | "2K" | "4K",
                    "aspect_ratio": "1:1" | "16:9" | etc,
                    "model": "gemini-3-pro-image-preview"  # オプション
                }

        Returns:
            {
                "success": bool,
                "image": {
                    "format": "png",
                    "data": "base64_encoded_string",
                    "resolution": "1024x1024"
                },
                "error": str  # エラー時のみ
            }
        """
        config = config or {}
        resolution = config.get("resolution", "1K")
        aspect_ratio = config.get("aspect_ratio", "1:1")
        model = config.get("model", cls.DEFAULT_MODEL)

        # リクエストボディ構築
        request_body = cls._build_request_body(prompt, resolution, aspect_ratio)

        # エンドポイントURL構築
        url = cls.BASE_URL.format(model=model)

        logger.info(
            f"Calling Gemini Image API: model={model}, "
            f"resolution={resolution}, prompt_length={len(prompt)}"
        )

        try:
            # Gemini API呼び出し
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    url,
                    json=request_body,
                    headers={
                        "Content-Type": "application/json",
                        "x-goog-api-key": api_key
                    }
                )

                response.raise_for_status()
                response_data = response.json()

            # レスポンスから画像データを抽出
            result = cls._extract_image_from_response(response_data)

            # 解像度情報を追加
            result["image"]["resolution"] = cls.RESOLUTION_MAP.get(resolution, resolution)

            logger.info(f"Image generation successful: resolution={resolution}")
            return result

        except httpx.HTTPStatusError as e:
            # 内部ログには詳細を記録、クライアントには一般的なメッセージ
            logger.warning(f"Gemini Image API error: {e.response.status_code} - {e.response.text}")
            return {
                "success": False,
                "error": f"Upstream API error: {e.response.status_code}"
            }

        except httpx.TimeoutException:
            error_msg = "Request timeout (60s)"
            logger.warning(error_msg)
            return {
                "success": False,
                "error": error_msg
            }

        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                "success": False,
                "error": "Internal server error"
            }

    @staticmethod
    def _build_request_body(
        prompt: str,
        resolution: str,
        aspect_ratio: str
    ) -> Dict[str, Any]:
        """
        Gemini API用のリクエストボディを構築

        generateContentメソッド用に、responseModalitiesを設定
        注: imageGenerationConfigは現在サポートされていないため削除
        """
        return {
            "contents": [
                {
                    "parts": [
                        {"text": prompt}
                    ]
                }
            ],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"]
            }
        }

    @staticmethod
    def _extract_image_from_response(response_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Gemini APIレスポンスから画像データを抽出

        レスポンス形式:
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "description..."},
                            {
                                "inlineData": {
                                    "mimeType": "image/png",
                                    "data": "base64_encoded_image"
                                }
                            }
                        ]
                    }
                }
            ]
        }
        """
        try:
            candidates = response_data.get("candidates", [])
            if not candidates:
                raise ValueError("No candidates in response")

            parts = candidates[0].get("content", {}).get("parts", [])

            # partsから画像データを探す
            for part in parts:
                if "inlineData" in part:
                    inline_data = part["inlineData"]
                    mime_type = inline_data.get("mimeType", "image/png")
                    image_data = inline_data.get("data")

                    if not image_data:
                        raise ValueError("No image data in inlineData")

                    # フォーマット推定
                    image_format = mime_type.split("/")[-1] if "/" in mime_type else "png"

                    return {
                        "success": True,
                        "image": {
                            "format": image_format,
                            "data": image_data
                        }
                    }

            raise ValueError("No inlineData found in response parts")

        except Exception as e:
            logger.error(f"Failed to extract image: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to extract image from response: {str(e)}"
            }
