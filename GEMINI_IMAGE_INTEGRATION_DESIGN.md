# Gemini 3 Pro Image 画像生成機能 統合設計書

**作成日**: 2025-12-18
**対象システム**: API Proxy Server
**機能**: Gemini 3 Pro Image (gemini-3-pro-image-preview) 画像生成API統合
**関連プロジェクト**: sogo-slide (クライアント側)

---

## 1. 概要

### 1.1 目的

OpenAI (DALL-E 3 / gpt-image-1) とGemini 3 Pro Imageの両方をサポートする統合画像生成エンドポイントを提供する。

### 1.2 重要な設計原則

**❌ OpenAIとGeminiのAPI仕様は完全に異なるため、共通化は不可能**

| 項目 | OpenAI | Gemini | 共通化可否 |
|------|--------|--------|----------|
| エンドポイント | `/v1/images/generations` | `/models/{model}:generateContent` | ❌ |
| リクエスト形式 | `{"prompt": "...", "size": "...", "quality": "..."}` | `{"contents": [...], "generationConfig": {...}}` | ❌ |
| レスポンス形式 | `{"data": [{"b64_json": "..."}]}` | `{"candidates": [...]}` | ❌ |

**✅ 採用するアプローチ: クライアント互換性を保ちつつ、サーバー側でプロバイダー別に変換**

---

## 2. アーキテクチャ設計

### 2.1 全体フロー

```
クライアント (sogo-slide)
    │
    │ OpenAI互換形式のリクエスト
    │ {
    │   "model": "gemini-3-pro-image-preview",
    │   "prompt": "...",
    │   "size": "1024x1024",
    │   "n": 1,
    │   "response_format": "b64_json"
    │ }
    ▼
認証サーバー (unified-auth-server)
    │ HMAC署名検証
    ▼
API Proxy Server
    │
    ├─ モデル名でプロバイダー判定
    │   │
    │   ├─ "gemini-3-pro-image-preview" → GeminiImageProvider
    │   │   │
    │   │   ├─ OpenAI形式 → Gemini形式に変換
    │   │   │   {
    │   │   │     "contents": [{"parts": [{"text": "..."}]}],
    │   │   │     "generationConfig": {
    │   │   │       "responseModalities": ["TEXT", "IMAGE"]
    │   │   │     }
    │   │   │   }
    │   │   ▼
    │   │   Gemini API呼び出し
    │   │   ▼
    │   │   Gemini形式レスポンス → OpenAI形式に変換
    │   │   {
    │   │     "created": 1234567890,
    │   │     "data": [{
    │   │       "b64_json": "...",
    │   │       "url": "data:image/png;base64,..."
    │   │     }]
    │   │   }
    │   │
    │   └─ "gpt-image-1" / "dall-e-3" → OpenAIProvider
    │       │
    │       └─ OpenAI形式のままAPIに送信
    │           ▼
    │           OpenAI API呼び出し
    │           ▼
    │           OpenAI形式レスポンスをそのまま返す
    │
    ▼
クライアントに OpenAI互換形式で返却
```

### 2.2 プロバイダー選択ロジック

**ファイル**: `app/upstream.py`

```python
async def call_ai_service(product_id: str, payload: dict, settings: Settings, endpoint_type: str = "chat") -> dict:
    model = payload.get("model")

    # プロバイダー判定
    if endpoint_type == "image":
        if model.startswith("gemini-"):
            # Gemini専用プロバイダーを使用
            from .providers.gemini_image import GeminiImageProvider

            api_key = get_gemini_api_key(product_id, settings)

            # OpenAI形式 → Gemini形式に変換してから呼び出し
            result = await GeminiImageProvider.generate_image(
                api_key=api_key,
                prompt=payload.get("prompt"),
                config={
                    "resolution": "1K",  # sizeから推定
                    "aspect_ratio": "1:1",
                    "model": model
                }
            )

            # Gemini形式レスポンス → OpenAI形式に変換
            return convert_gemini_to_openai_format(result)
        else:
            # OpenAI画像生成
            return await OpenAIProvider.call_image(
                api_key=get_openai_api_key(product_id, settings),
                payload=payload,
                base_url=None,
                timeout=settings.request_timeout_seconds
            )
```

---

## 3. リクエスト形式の詳細

### 3.1 クライアントから受け取る形式（OpenAI互換）

```json
{
  "model": "gemini-3-pro-image-preview",
  "prompt": "A cute cat sitting on a windowsill",
  "size": "1024x1024",
  "n": 1,
  "response_format": "b64_json"
}
```

**パラメータ**:
- `model`: モデル名（プロバイダー判定に使用）
- `prompt`: 画像生成プロンプト
- `size`: 画像サイズ（OpenAI形式: "1024x1024", "2048x2048" 等）
- `n`: 生成枚数（現在は1のみサポート）
- `response_format`: レスポンス形式（"b64_json" または "url"）

### 3.2 Gemini APIに送る形式（変換後）

```json
{
  "contents": [
    {
      "parts": [
        {"text": "A cute cat sitting on a windowsill"}
      ]
    }
  ],
  "generationConfig": {
    "responseModalities": ["TEXT", "IMAGE"]
  }
}
```

**重要な変換ルール**:
1. `prompt` → `contents[0].parts[0].text`
2. `responseModalities: ["TEXT", "IMAGE"]` は**必須**
3. `size`パラメータは現在Gemini APIでサポートされていない（将来対応予定）

---

## 4. レスポンス形式の詳細

### 4.1 Gemini APIから受け取る形式

```json
{
  "candidates": [
    {
      "content": {
        "parts": [
          {
            "text": "Generated image description..."
          },
          {
            "inlineData": {
              "mimeType": "image/png",
              "data": "iVBORw0KGgoAAAANSUhEUg..."
            }
          }
        ]
      },
      "finishReason": "STOP"
    }
  ]
}
```

### 4.2 クライアントに返す形式（OpenAI互換）

```json
{
  "created": 1734567890,
  "data": [
    {
      "b64_json": "iVBORw0KGgoAAAANSUhEUg...",
      "url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg..."
    }
  ]
}
```

**変換ロジック**:
```python
def convert_gemini_to_openai_format(gemini_response: dict) -> dict:
    """
    Gemini APIレスポンスをOpenAI互換形式に変換
    """
    # candidates[0].content.parts から画像データを抽出
    candidates = gemini_response.get("candidates", [])
    if not candidates:
        raise ValueError("No candidates in Gemini response")

    parts = candidates[0].get("content", {}).get("parts", [])

    # inlineDataを含むpartを探す
    image_b64 = None
    for part in parts:
        if "inlineData" in part:
            image_b64 = part["inlineData"].get("data")
            break

    if not image_b64:
        raise ValueError("No image data found in Gemini response")

    # OpenAI形式に変換
    return {
        "created": int(time.time()),
        "data": [
            {
                "b64_json": image_b64,
                "url": f"data:image/png;base64,{image_b64}"
            }
        ]
    }
```

---

## 5. GeminiImageProvider 実装仕様

### 5.1 クラス構造

**ファイル**: `app/providers/gemini_image.py`（既存）

```python
class GeminiImageProvider:
    """Gemini 3 Pro Image専用プロバイダー"""

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    DEFAULT_MODEL = "gemini-3-pro-image-preview"

    @classmethod
    async def generate_image(
        cls,
        api_key: str,
        prompt: str,
        config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        画像生成メインメソッド

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
```

### 5.2 upstream.pyでの統合

**修正が必要な箇所**: `app/upstream.py:60-66`

```python
# 現状の問題あるコード
if endpoint_type == "image":
    return await provider_class.call_image(...)  # ← GeminiProviderにcall_imageがあるが不完全

# 修正後の正しいコード
if endpoint_type == "image":
    if model.startswith("gemini-"):
        # Gemini専用プロバイダーを使用
        from .providers.gemini_image import GeminiImageProvider

        # OpenAI形式のpayloadからGemini用のパラメータを抽出
        prompt = payload.get("prompt")
        size = payload.get("size", "1024x1024")

        # 解像度の変換（OpenAI形式 → Gemini形式）
        resolution_map = {
            "1024x1024": "1K",
            "2048x2048": "2K",
            "4096x4096": "4K"
        }
        resolution = resolution_map.get(size, "1K")

        # Gemini画像生成呼び出し
        result = await GeminiImageProvider.generate_image(
            api_key=provider_config.api_key,
            prompt=prompt,
            config={
                "resolution": resolution,
                "aspect_ratio": "1:1",
                "model": model
            }
        )

        # Gemini形式 → OpenAI形式に変換
        if not result.get("success"):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=result.get("error", "Image generation failed")
            )

        return {
            "created": int(time.time()),
            "data": [{
                "b64_json": result["image"]["data"],
                "url": f"data:image/png;base64,{result['image']['data']}"
            }]
        }
    else:
        # OpenAI画像生成（既存処理）
        return await provider_class.call_image(
            api_key=provider_config.api_key,
            payload=payload,
            base_url=provider_config.base_url,
            timeout=settings.request_timeout_seconds
        )
```

---

## 6. GeminiProvider.call_image() の削除

### 6.1 問題のあるコード

**ファイル**: `app/providers/gemini.py:138-251`

このメソッドは**削除または非推奨**にすべき。理由：

1. OpenAI形式のリクエストをGemini APIに直接送っている（400エラーの原因）
2. 変換ロジックが不完全
3. `GeminiImageProvider`が専用実装として存在する

### 6.2 推奨対応

**オプション1: 完全削除**
```python
# app/providers/gemini.py
class GeminiProvider:
    @staticmethod
    async def call(...):
        """テキスト生成のみサポート"""
        pass

    @staticmethod
    async def call_audio(...):
        """音声生成のみサポート"""
        pass

    # call_image() は削除
```

**オプション2: リダイレクト実装**
```python
@staticmethod
async def call_image(api_key: str, payload: dict, base_url: str | None = None, timeout: int = 30) -> dict:
    """
    画像生成（GeminiImageProviderにリダイレクト）

    Note: このメソッドは互換性のために残されています。
    新規実装では GeminiImageProvider.generate_image() を直接使用してください。
    """
    from .gemini_image import GeminiImageProvider

    # OpenAI形式 → Gemini形式に変換
    prompt = payload.get("prompt")
    model = payload.get("model", GeminiImageProvider.DEFAULT_MODEL)

    result = await GeminiImageProvider.generate_image(
        api_key=api_key,
        prompt=prompt,
        config={"model": model}
    )

    # Gemini形式 → OpenAI形式に変換して返す
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Image generation failed")
        )

    return {
        "created": int(time.time()),
        "data": [{
            "b64_json": result["image"]["data"],
            "url": f"data:image/png;base64,{result['image']['data']}"
        }]
    }
```

---

## 7. エラーハンドリング

### 7.1 主要なエラーケース

| エラー種別 | HTTPステータス | 処理 |
|-----------|---------------|------|
| プロンプトが空 | 400 | バリデーションエラー |
| モデル名が不正 | 400 | サポートされていないモデル |
| Gemini API 400 | 400 | リクエストパラメータエラー |
| Gemini API 401/403 | 502 | 認証失敗（上流） |
| Gemini API 500+ | 502 | 上流サービスエラー |
| タイムアウト | 504 | Gateway Timeout |
| 画像データ抽出失敗 | 500 | レスポンス形式エラー |

### 7.2 ログ出力

**必須ログポイント**:
```python
# リクエスト受信時
logger.info(f"Image generation request: model={model}, provider=gemini")

# Gemini API呼び出し前
logger.info(f"Calling Gemini Image API: model={model}, prompt_length={len(prompt)}")

# 成功時
logger.info(f"Gemini image generation successful: size={len(image_data)} bytes")

# エラー時（内部ログのみ、クライアントには返さない）
logger.error(f"Gemini API error: status_code={response.status_code}", exc_info=True)
```

---

## 8. フォールバック戦略

### 8.1 現在の実装

**フォールバックあり**: Gemini失敗時に自動的にOpenAIに切り替え

**問題点**:
- ユーザーが意図しないモデルで画像が生成される
- コストが異なる（Gemini: $0.067/枚、OpenAI: $0.04/枚）
- 品質が異なる（テキストレンダリング等）

### 8.2 推奨戦略

**フォールバックなし**: エラーをそのまま返す

**理由**:
1. ユーザーが明示的にGeminiを選択している
2. コスト・品質が異なるモデルへの自動切り替えは不適切
3. クライアント側でリトライ・フォールバックを制御すべき

**実装**:
```python
# upstream.py
if model.startswith("gemini-"):
    try:
        result = await GeminiImageProvider.generate_image(...)
    except Exception as e:
        # フォールバックせず、エラーを返す
        logger.error(f"Gemini image generation failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Image generation failed"
        )
```

---

## 9. パフォーマンス・コスト

### 9.1 タイムアウト設定

| 処理 | タイムアウト |
|------|------------|
| Gemini API呼び出し | 60秒 |
| 全体リクエスト | 300秒（5分） |

### 9.2 コスト

| モデル | 解像度 | コスト/枚 |
|--------|--------|----------|
| Gemini 3 Pro Image | 1K (1024x1024) | $0.067 |
| Gemini 3 Pro Image | 2K (2048x2048) | $0.134 |
| Gemini 3 Pro Image | 4K (4096x4096) | $0.24 |
| OpenAI gpt-image-1 | 1024x1024 | $0.04 |
| OpenAI dall-e-3 | 1024x1024 | $0.04 |

---

## 10. テスト計画

### 10.1 単体テスト

**ファイル**: `app/test/test_gemini_image_provider.py`

```python
import pytest
from app.providers.gemini_image import GeminiImageProvider

@pytest.mark.asyncio
async def test_generate_image_success():
    """正常系: 画像生成成功"""
    result = await GeminiImageProvider.generate_image(
        api_key="test-key",
        prompt="A red apple",
        config={"resolution": "1K"}
    )

    assert result["success"] == True
    assert "image" in result
    assert "data" in result["image"]

@pytest.mark.asyncio
async def test_generate_image_empty_prompt():
    """異常系: 空のプロンプト"""
    result = await GeminiImageProvider.generate_image(
        api_key="test-key",
        prompt="",
        config={}
    )

    assert result["success"] == False
    assert "error" in result
```

### 10.2 統合テスト

```bash
# Gemini画像生成エンドポイントのテスト
curl -X POST "http://localhost:8000/v1/images/generations/product-SlideVideo" \
  -H "X-Client-ID: test-client" \
  -H "X-Signature: <signature>" \
  -H "X-Timestamp: <timestamp>" \
  -d '{
    "model": "gemini-3-pro-image-preview",
    "prompt": "A cute cat",
    "size": "1024x1024",
    "n": 1,
    "response_format": "b64_json"
  }'
```

**期待結果**:
```json
{
  "created": 1734567890,
  "data": [{
    "b64_json": "iVBORw0KG...",
    "url": "data:image/png;base64,iVBORw0KG..."
  }]
}
```

---

## 11. デプロイ・設定

### 11.1 環境変数

**ファイル**: `.env`

```bash
# Gemini API設定
GEMINI_API_KEY=your-gemini-api-key-here

# タイムアウト設定
GEMINI_IMAGE_TIMEOUT=60
REQUEST_TIMEOUT_SECONDS=300
```

### 11.2 プロダクト設定

**ファイル**: `config.toml` または環境変数

```toml
[products.product-SlideVideo]
allowed_models = [
    "gpt-image-1",
    "dall-e-3",
    "gemini-3-pro-image-preview"
]

[products.product-SlideVideo.providers.gemini]
api_key = "${GEMINI_API_KEY}"
models = ["gemini-3-pro-image-preview"]
```

---

## 12. まとめ

### 12.1 実装の要点

1. ✅ **OpenAIとGeminiは完全分離**: 共通化は不可能
2. ✅ **クライアント互換性**: OpenAI形式でリクエスト受信
3. ✅ **サーバー側変換**: モデル名で判定し、適切なプロバイダーに変換
4. ✅ **レスポンス統一**: すべてOpenAI形式で返却
5. ❌ **フォールバックなし**: エラーはそのまま返す

### 12.2 主要な修正箇所

| ファイル | 修正内容 |
|---------|---------|
| `app/upstream.py` | Gemini判定ロジック追加、GeminiImageProvider統合 |
| `app/providers/gemini.py` | `call_image()`削除または修正 |
| `app/providers/gemini_image.py` | 既存実装を維持（変更なし） |

### 12.3 次のステップ

1. この設計書に基づいて`app/upstream.py`を修正
2. `app/providers/gemini.py`の`call_image()`を削除
3. 統合テスト実施
4. Cloud Runにデプロイ

---

## 更新履歴

- **2025-12-18**: 初版作成（OpenAI vs Gemini仕様比較に基づく設計）
