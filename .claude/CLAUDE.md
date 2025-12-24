# プロジェクト AI ガイド（API Key Proxy Server）

> このファイルは、**API Key Proxy Server 専用の AI 向けルール集**です。
> 共通ルール（`~/.claude/AI_COMMON_RULES.md`）に加えて、このプロジェクト固有の前提・例外・開発規約を定義します。

---

## 1. プロジェクト概要

- **プロジェクト名**: API Key Proxy Server
- **概要**:
  - FastAPIベースのマイクロサービス
  - 複数のAI API（OpenAI、Google Gemini、Anthropic Claude）へのアクセスを一元管理
  - 上流APIキーをサーバー側で安全に管理し、クライアント側での保持を不要に
  - 認証サーバー（auth-server）と連携したプロジェクト別アクセス制御
- **想定ユーザー**:
  - 内部アプリケーション（Streamlit、Webアプリ等）
  - 認証済みクライアントのみがアクセス可能

---

## 2. 技術スタック（このプロジェクト専用）

### 2.1 フレームワーク・言語
- **Python**: 3.11
- **FastAPI**: 0.111.0
- **Uvicorn**: 0.30.1（ASGIサーバー）
- **Pydantic**: 2.7.4（データ検証）
- **HTTPX**: 0.27.0（非同期HTTPクライアント）

### 2.2 セキュリティ・認証
- **Python-Jose**: 3.3.0（JWT/JWE処理）
- **Google Cloud Secret Manager**: 2.20.0
- **Google Auth**: 2.43.0（IAP認証）

### 2.3 インフラ
- **Redis**: 5.0.4（レート制限キャッシュ、オプション）
- **Docker**: Python 3.11-slim ベース
- **Google Cloud Run**: メインデプロイ環境

### 2.4 外部AI API
1. **OpenAI**: gpt-4o、gpt-4o-mini、DALL-E 3、TTS
2. **Google Gemini**: gemini-1.5-pro、gemini-1.5-flash
3. **Anthropic Claude**: claude-3-5-sonnet、claude-3-opus

> AI への指示例：
> 「このプロジェクトでは Python 3.11 と FastAPI を使用します。
> 非同期処理は async/await で実装し、Pydantic でデータ検証を行ってください。」

---

## 3. ディレクトリ構成ルール

```text
api-key-server/
├── app/
│   ├── main.py                 # FastAPIアプリケーション本体
│   ├── config.py               # 設定管理（Pydantic BaseSettings）
│   ├── auth.py                 # 認証処理（JWT、HMAC、IAP）
│   ├── rate_limit.py           # Token Bucketアルゴリズム実装
│   ├── secrets.py              # Secret Manager統合
│   ├── upstream.py             # 上流API呼び出し・プロバイダ委譲
│   └── providers/
│       ├── openai.py           # OpenAIプロバイダ
│       ├── gemini.py           # Google Geminiプロバイダ
│       └── anthropic.py        # Anthropic Claudeプロバイダ
├── config/                     # 設定ファイル（ローカル開発用）
├── docs/                       # ドキュメント
├── tests/                      # テストファイル（未実装）
├── scripts/                    # 運用スクリプト
├── DESIGN.md                   # 設計書
├── README.md                   # プロジェクトREADME
├── requirements.txt            # Python依存関係
└── Dockerfile                  # コンテナイメージ定義
```

### ディレクトリに関するルール

- `app/`: すべてのアプリケーションコードを配置
- `app/providers/`: 各AI APIプロバイダの実装（統一インターフェース）
- `config/`: ローカル開発用の設定ファイル（本番ではSecret Manager使用）
- `tests/`: 単体テスト・統合テスト（未実装、実装が必要）

> AI への指示例：
> 「新しいAIプロバイダを追加する場合は、app/providers/以下に<provider_name>.pyを作成し、
> call()、call_image()、call_audio()メソッドを実装してください。」

---

## 4. コーディング規約（このプロジェクト専用）

### 4.1 Python コーディングスタイル

```python
# ✅ 良い例：型ヒント付き非同期関数
from typing import Optional, Dict, Any
import httpx

async def call_upstream_api(
    api_key: str,
    payload: Dict[str, Any],
    endpoint: str,
    timeout: int = 90
) -> Dict[str, Any]:
    """
    上流APIを呼び出す

    Args:
        api_key: APIキー
        payload: リクエストボディ
        endpoint: エンドポイントURL
        timeout: タイムアウト秒数

    Returns:
        APIレスポンス

    Raises:
        httpx.HTTPStatusError: HTTPエラー時
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(endpoint, json=payload, headers={...})
        response.raise_for_status()
        return response.json()
```

### 4.2 FastAPI特有のパターン

```python
# ✅ 良い例：依存性注入とHTTPException
from fastapi import HTTPException, status, Depends
from app.auth import verify_request
from app.config import Settings, get_settings

@app.post("/v1/chat/{product}")
async def proxy_chat(
    product: str,
    request: ChatRequest,
    context: AuthContext = Depends(verify_request),
    settings: Settings = Depends(get_settings)
):
    """チャット補完エンドポイント"""
    # 認証済みコンテキストを使用
    logger.info(f"Processing request for user: {context.user_id}")

    if not product_config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product '{product}' not found"
        )
```

### 4.3 Pydantic バリデーション

```python
# ✅ 良い例：カスタムバリデータ
from pydantic import BaseModel, validator

class ChatRequest(BaseModel):
    model: str
    messages: list
    max_tokens: Optional[int] = None

    @validator("max_tokens")
    def validate_max_tokens(cls, value, values, config):
        """max_tokensの上限チェック"""
        if value and value > config.context.get("settings").max_tokens:
            raise ValueError(
                f"max_tokens must be <= {config.context.get('settings').max_tokens}"
            )
        return value
```

### 4.4 エラーハンドリングの統一

```python
# ✅ 良い例：上流エラーの隠蔽
try:
    response = await provider.call(api_key, payload, timeout)
    return response
except httpx.HTTPStatusError as e:
    # 上流のエラー詳細は隠蔽（セキュリティ対策）
    logger.error(f"Upstream API error: {e.response.status_code}")
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Upstream service error"
    )
except Exception as e:
    logger.exception(f"Unexpected error: {str(e)}")
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Internal server error"
    )
```

---

## 5. 認証メカニズム（3方式対応）

このプロジェクトは3つの認証方式をサポートしています。

### 5.1 JWT (RS256)

**リクエストヘッダー**:
```
Authorization: Bearer <jwt_token>
```

**検証フロー**:
1. トークンから`kid`（キーID）を取得
2. 対応する公開鍵をSecret Managerから取得
3. RS256アルゴリズムで署名検証
4. `aud`（audience）クレームをチェック

**実装箇所**: [app/auth.py:_verify_jwt()](app/auth.py)

### 5.2 HMAC + タイムスタンプ

**リクエストヘッダー**:
```
X-Timestamp: <unix_timestamp>
X-Signature: <hmac_sha256_signature>
X-Client-ID: <client_id>
```

**署名生成方法**:
```python
import hmac
import hashlib
import json

timestamp = str(int(time.time()))
method = "POST"
path = "/v1/chat/product-a"
body_hash = hashlib.sha256(json.dumps(body).encode()).hexdigest()

message = f"{timestamp}\n{method}\n{path}\n{body_hash}"
signature = hmac.new(
    client_secret.encode(),
    message.encode(),
    hashlib.sha256
).hexdigest()
```

**リプレイ攻撃対策**:
- タイムスタンプの許容範囲: デフォルト300秒
- 環境変数 `API_KEY_SERVER_HMAC_CLOCK_TOLERANCE_SECONDS` で調整可能

**実装箇所**: [app/auth.py:_verify_hmac()](app/auth.py)

### 5.3 Google IAP JWT

**リクエストヘッダー**:
```
X-Goog-IAP-JWT-Assertion: <iap_jwt>
```

**検証フロー**:
1. Googleの公開鍵を自動取得
2. JWTの署名を検証
3. `email`クレームからユーザー情報を抽出

**実装箇所**: [app/auth.py:_verify_iap()](app/auth.py)

> AI への指示例：
> 「認証エラー時は必ず HTTP 401 Unauthorized を返してください。
> エラー詳細はログに記録し、クライアントには一般的なメッセージのみ返してください。」

---

## 6. マルチプロバイダ対応

### 6.1 プロダクト設定フォーマット

**新形式（マルチプロバイダ推奨）**:
```json
{
  "product-a": {
    "providers": {
      "openai": {
        "api_key": "sk-proj-xxxxx",
        "models": ["gpt-4o", "gpt-4o-mini", "dall-e-3"]
      },
      "gemini": {
        "api_key": "AIzaSy...",
        "models": ["gemini-1.5-pro", "gemini-1.5-flash"]
      },
      "anthropic": {
        "api_key": "sk-ant-...",
        "models": ["claude-3-5-sonnet-20241022"]
      }
    }
  }
}
```

**旧形式（レガシー互換性、OpenAIのみ）**:
```json
{
  "product-a": "sk-xxxxx"
}
```

### 6.2 プロバイダ選択ロジック

1. リクエストの`model`パラメータを取得
2. プロダクト設定から各プロバイダの`models`リストを確認
3. `models`が空リスト または `model`が含まれていればそのプロバイダを使用
4. マッチしなければ `400 Bad Request`

**実装箇所**: [app/upstream.py:select_provider()](app/upstream.py)

### 6.3 プロバイダインターフェース

すべてのプロバイダは以下のメソッドを実装する必要があります:

```python
# app/providers/<provider_name>.py

async def call(api_key: str, payload: dict, timeout: int) -> dict:
    """
    チャット補完API呼び出し

    Args:
        api_key: プロバイダのAPIキー
        payload: OpenAI互換形式のリクエストボディ
        timeout: タイムアウト秒数

    Returns:
        OpenAI互換形式のレスポンス
    """
    # 1. リクエスト変換
    provider_request = _convert_request(payload)

    # 2. 上流API呼び出し
    response = await _call_upstream(api_key, provider_request, timeout)

    # 3. レスポンス変換
    return _convert_response(response)

async def call_image(api_key: str, payload: dict, timeout: int) -> dict:
    """画像生成API呼び出し"""
    pass

async def call_audio(api_key: str, payload: dict, timeout: int) -> dict:
    """音声生成API呼び出し"""
    pass
```

**重要**: すべてのレスポンスはOpenAI互換形式に統一してください。

> AI への指示例：
> 「新しいプロバイダを追加する場合は、既存のopenai.pyをテンプレートとして使用し、
> _convert_request()と_convert_response()でフォーマット変換を実装してください。」

---

## 7. レート制限実装

### 7.1 Token Bucket アルゴリズム

**デフォルト設定**:
- **容量**: 10 リクエスト
- **補充レート**: 5 tokens/sec
- **日次クォータ**: 200,000 リクエスト

**実装箇所**: [app/rate_limit.py](app/rate_limit.py)

### 7.2 バックエンド選択

**インメモリモード**（デフォルト）:
```python
# asyncio.Lockでスレッドセーフ
limiter = RateLimiter(redis_url=None)
```

**Redisモード**（分散環境推奨）:
```python
# Luaスクリプトでアトミック操作
limiter = RateLimiter(redis_url="redis://localhost:6379/0")
```

**環境変数**:
```bash
API_KEY_SERVER_REDIS_URL=redis://localhost:6379/0
```

### 7.3 エラーレスポンス

```json
{
  "detail": "Rate limit exceeded. Try again in 2 seconds"
}
```

```json
{
  "detail": "Daily quota exceeded (200000 requests/day)"
}
```

> AI への指示例：
> 「レート制限エラーは必ず HTTP 429 Too Many Requests で返してください。
> エラーメッセージには次回リトライまでの待機時間を含めてください。」

---

## 8. Secret Manager 統合

### 8.1 有効化フロー

**環境変数**:
```bash
USE_SECRET_MANAGER=true
API_KEY_SERVER_GCP_PROJECT_ID=your-project-id
```

### 8.2 シークレット構成

| シークレット名 | 形式 | 内容 |
|--------------|------|------|
| `openai-api-keys` | JSON | プロダクト設定 |
| `jwt-public-keys` | JSON | JWT公開鍵リスト |
| `hmac-secrets` | JSON | HMACシークレットリスト |

**プロダクト設定例** (`openai-api-keys`):
```json
{
  "product-a": {
    "providers": {
      "openai": {"api_key": "sk-...", "models": ["gpt-4o"]}
    }
  }
}
```

**JWT公開鍵例** (`jwt-public-keys`):
```json
{
  "key-id-1": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----"
}
```

### 8.3 ローカル開発

Secret Managerを使用しない場合:
```bash
USE_SECRET_MANAGER=false
```

ローカルファイルから読み込み:
- `config/openai_api_keys.json`
- `config/jwt_public_keys.json`
- `config/hmac_secrets.json`

> AI への指示例：
> 「Secret Manager関連のエラーは WARNING レベルでログに記録し、
> ローカルファイルへのフォールバックを試みてください。」

---

## 9. エラーハンドリング・ログ出力

### 9.1 HTTPステータスコード

| ステータス | 意味 | 使用場面 |
|-----------|------|----------|
| 200 | 成功 | API呼び出し成功 |
| 400 | クライアントエラー | max_tokens超過、モデル未対応、ストリーミング要求 |
| 401 | 認証失敗 | JWT無効、HMAC署名不一致、タイムスタンプ超過 |
| 403 | 認可エラー | product_id ミスマッチ |
| 404 | 未発見 | プロダクト未登録 |
| 429 | レート制限 | QPS超過、日次クォータ超過 |
| 501 | 未実装 | プロバイダ未実装、ストリーミング |
| 502 | 上流エラー | OpenAI/Gemini サービスダウン、APIキー無効 |
| 500 | 内部エラー | 予期しない例外 |

**重要**: 上流APIのエラー詳細は隠蔽し、すべて `502 Bad Gateway` として返す（セキュリティ対策）

### 9.2 ログ出力方針

#### 運用ログ（恒久的）
- **INFO**: API呼び出し開始（プロダクト、ユーザー、モデル）
- **INFO**: プロバイダ選択結果
- **INFO**: IAP認証成功
- **INFO**: Secret Manager シークレット取得
- **WARNING**: Secret Manager ロード失敗（フォールバック時）
- **ERROR**: Secret Manager 完全失敗、JWT検証失敗、上流APIエラー

#### ログフォーマット

```python
import logging

logger = logging.getLogger(__name__)

# ✅ 良い例：構造化ログ
logger.info(
    "proxying request",
    extra={
        "product": product,
        "user": context.user_id,
        "method": context.method,
        "model": payload.model,
    }
)

# ✅ 良い例：エラーログ
logger.error(
    f"Upstream API error: {e.response.status_code}",
    extra={
        "product": product,
        "model": payload.model,
        "status_code": e.response.status_code,
    },
    exc_info=True
)
```

### 9.3 セキュリティ関連のログ

```python
# ✅ APIキーは絶対にログに出力しない
logger.error(f"Invalid API key for product: {product}")  # OK
logger.error(f"API key: {api_key}")  # NG - 絶対にやらない

# ✅ ユーザー識別情報のみ記録
logger.info(f"Authentication failed for user: {context.user_id}")
```

> AI への指示例：
> 「APIキーやシークレットは絶対にログに出力しないでください。
> 上流APIのエラー詳細はログに記録しますが、クライアントには返さないでください。」

---

## 10. テスト方針（未実装、実装が必要）

### 10.1 単体テスト

```python
# tests/test_auth.py
import pytest
from app.auth import verify_request
from fastapi import HTTPException

def test_jwt_verification_success():
    """JWT検証の成功ケース"""
    # モックJWTトークンを使用
    pass

def test_jwt_verification_invalid_signature():
    """JWT署名検証の失敗ケース"""
    with pytest.raises(HTTPException) as exc_info:
        # 無効な署名のトークンを検証
        pass
    assert exc_info.value.status_code == 401
```

### 10.2 統合テスト

```python
# tests/test_integration.py
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_chat_endpoint_with_jwt():
    """チャットエンドポイントのJWT認証テスト"""
    response = client.post(
        "/v1/chat/product-a",
        headers={"Authorization": "Bearer <valid_jwt>"},
        json={"model": "gpt-4o", "messages": [...]}
    )
    assert response.status_code == 200
```

### 10.3 重要なテスト項目

- [ ] JWT認証（RS256）の成功・失敗
- [ ] HMAC認証の成功・失敗・タイムスタンプ超過
- [ ] IAP認証の成功・失敗
- [ ] レート制限の動作確認（QPS、日次クォータ）
- [ ] 各プロバイダ（OpenAI、Gemini、Anthropic）の呼び出し
- [ ] プロバイダ自動選択ロジック
- [ ] エラーハンドリング（上流エラーの隠蔽）
- [ ] Secret Manager統合

> AI への指示例：
> 「テストコードを追加する場合は、tests/ディレクトリ以下に配置し、
> pytest形式で実装してください。モックは unittest.mock を使用してください。」

---

## 11. パフォーマンス・スケーラビリティ

### 11.1 タイムアウト設定

| パラメータ | デフォルト | 環境変数 |
|-----------|-----------|---------|
| HTTPタイムアウト | 90秒 | `API_KEY_SERVER_REQUEST_TIMEOUT_SECONDS` |
| max_tokens上限 | 2048 | `API_KEY_SERVER_MAX_TOKENS` |

### 11.2 スケーラビリティ

**インメモリモードの制限**:
- 複数インスタンス間でレート制限が共有されない
- Cloud Runのオートスケーリング時は各インスタンスが独立

**Redisモードの利点**:
- 全インスタンスでレート制限を共有
- Luaスクリプトによるアトミック操作
- 分散環境に最適

**推奨構成**:
```bash
# 本番環境
API_KEY_SERVER_REDIS_URL=redis://your-redis-instance:6379/0

# ローカル開発
API_KEY_SERVER_REDIS_URL=  # 未設定でインメモリモード
```

---

## 12. auth-server との連携

### 12.1 連携フロー

1. **JWT トークン発行**: auth-server が JWT 発行 → このサーバーで RS256 検証
2. **HMAC 署名**: auth-server がクライアント個別の `client_secret` を保管 → このサーバーで検証
3. **プロジェクト設定**: Firestore に保存されたプロジェクト設定から `product_id` を取得 → このサーバーで API キー選択

### 12.2 HMAC署名形式の注意

**このサーバーの検証ロジック**:
```python
message = f"{timestamp}\n{method}\n{path}\n{body_hash}"
expected_signature = hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()
```

**auth-server側での署名生成**:
auth-server側で同じ形式で署名を生成する必要があります。

**検証項目**:
- [ ] タイムスタンプ形式が一致（UNIX timestamp）
- [ ] HTTPメソッドが一致（大文字）
- [ ] パスが一致（/v1/chat/product-a）
- [ ] ボディのハッシュ化方法が一致（SHA256）

> AI への指示例：
> 「auth-serverとの連携時は、HMAC署名形式が完全に一致していることを確認してください。
> 不一致がある場合は、両方のサーバーのログを比較して原因を特定してください。」

---

## 13. 既知の制限・注意事項

### 13.1 ストリーミング非対応

```python
if payload.stream:
    raise HTTPException(
        status_code=400,
        detail="Streaming is not supported"
    )
```

**理由**: 現在の実装では非ストリーミングのみ対応

### 13.2 テスト未実装

`tests/` ディレクトリは作成されているが、テストコードはありません。

**対策**: 実装を優先的に進める必要があります。

### 13.3 未使用ディレクトリ

以下のディレクトリは作成されているが使用されていません:
- `models/`
- `routes/`
- `services/`
- `utils/`

**対策**: 将来の拡張に備えて残しておく、または削除する。

---

## 14. 新プロバイダ追加ガイドライン

### 14.1 実装チェックリスト

- [ ] `app/providers/<provider_name>.py` を作成
- [ ] `call()` メソッド実装（チャット補完）
- [ ] `call_image()` メソッド実装（画像生成）
- [ ] `call_audio()` メソッド実装（音声生成）
- [ ] `_convert_request()` で OpenAI 形式 → プロバイダ形式に変換
- [ ] `_convert_response()` で プロバイダ形式 → OpenAI 形式に変換
- [ ] `app/upstream.py` の `PROVIDERS` 辞書に登録
- [ ] テストコード追加
- [ ] ドキュメント更新

### 14.2 実装テンプレート

```python
# app/providers/<provider_name>.py
import httpx
from typing import Dict

async def call(api_key: str, payload: dict, timeout: int) -> dict:
    """チャット補完"""
    request = _convert_request(payload)
    response = await _call_upstream(api_key, request, timeout)
    return _convert_response(response)

async def call_image(api_key: str, payload: dict, timeout: int) -> dict:
    """画像生成"""
    raise NotImplementedError("Image generation not supported")

async def call_audio(api_key: str, payload: dict, timeout: int) -> dict:
    """音声生成"""
    raise NotImplementedError("Audio generation not supported")

def _convert_request(payload: dict) -> dict:
    """OpenAI形式 → プロバイダ形式"""
    return {
        # プロバイダ固有の形式に変換
    }

def _convert_response(response: dict) -> dict:
    """プロバイダ形式 → OpenAI形式"""
    return {
        "id": response.get("id"),
        "object": "chat.completion",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": response.get("content")
                }
            }
        ]
    }

async def _call_upstream(api_key: str, request: dict, timeout: int) -> dict:
    """上流API呼び出し"""
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            "https://api.provider.com/v1/endpoint",
            json=request,
            headers={"Authorization": f"Bearer {api_key}"}
        )
        response.raise_for_status()
        return response.json()
```

---

## 15. 運用手順書

### 15.1 Secret Manager メンテナンス

**シークレット更新**:
```bash
# プロダクト設定の更新
gcloud secrets versions add openai-api-keys \
  --data-file=config/openai_api_keys.json \
  --project=your-project-id

# JWT公開鍵の更新
gcloud secrets versions add jwt-public-keys \
  --data-file=config/jwt_public_keys.json \
  --project=your-project-id
```

**確認**:
```bash
# 最新バージョンの確認
gcloud secrets versions list openai-api-keys --project=your-project-id

# シークレットの読み取り
gcloud secrets versions access latest \
  --secret=openai-api-keys \
  --project=your-project-id
```

### 15.2 本番デプロイ

**Cloud Run デプロイ**:
```bash
# Dockerイメージビルド
docker build -t gcr.io/your-project-id/api-key-server:latest .

# イメージプッシュ
docker push gcr.io/your-project-id/api-key-server:latest

# Cloud Runデプロイ（初回）
gcloud run deploy api-key-server \
  --image gcr.io/your-project-id/api-key-server:latest \
  --platform managed \
  --region asia-northeast1 \
  --allow-unauthenticated \
  --set-env-vars USE_SECRET_MANAGER=true,API_KEY_SERVER_GCP_PROJECT_ID=your-project-id,API_KEY_SERVER_MAX_TOKENS=8192
```

**⚠️ 重要：環境変数の管理ルール（変数削除を防ぐ）**

`--set-env-vars` オプションは**既存の環境変数を完全に上書き**します。新しい変数を追加する際に既存の変数が削除されることを防ぐため、以下のルールを厳守してください。

**gcloud コマンドの環境変数オプション**:

| オプション | 動作 | 使用場面 |
|-----------|------|----------|
| `--set-env-vars=A=1,B=2` | **すべての既存変数を削除**して、A=1, B=2 のみ設定 | 環境変数を完全に置き換える時 |
| `--update-env-vars=C=3` | 既存変数を保持して、C=3 を追加または更新 | **新しい変数を追加する時（推奨）** |
| `--remove-env-vars=D` | 指定した変数Dのみを削除、他は保持 | 特定の変数を削除する時 |
| `--clear-env-vars` | すべての環境変数を削除 | 環境変数をクリアする時 |

**環境変数を追加・変更する正しい手順**:

1. **現在の環境変数を確認**:
   ```bash
   gcloud run services describe api-key-server \
     --region=asia-northeast1 \
     --format="yaml(spec.template.spec.containers[0].env)" \
     --project=interview-api-472500
   ```

2. **`--update-env-vars` で追加（推奨）**:
   ```bash
   # 既存の変数を保持したまま、新しい変数を追加または更新
   gcloud run services update api-key-server \
     --region=asia-northeast1 \
     --update-env-vars API_KEY_SERVER_MAX_TOKENS=8192 \
     --project=interview-api-472500
   ```

3. **または、すべての変数を含めて `--set-env-vars`**:
   ```bash
   # すべての必須環境変数を明示的に指定
   gcloud run services update api-key-server \
     --region=asia-northeast1 \
     --set-env-vars USE_SECRET_MANAGER=true,API_KEY_SERVER_GCP_PROJECT_ID=interview-api-472500,API_KEY_SERVER_MAX_TOKENS=8192 \
     --project=interview-api-472500
   ```

**現在の必須環境変数**:
- `USE_SECRET_MANAGER=true` - Secret Manager有効化
- `API_KEY_SERVER_GCP_PROJECT_ID=interview-api-472500` - GCPプロジェクトID
- `API_KEY_SERVER_MAX_TOKENS=8192` - トークン上限（ソースコードのデフォルト値があるため省略可能だが、明示推奨）

**デプロイ前のチェックリスト**:
- [ ] 現在の環境変数を確認した
- [ ] `--update-env-vars` を使用するか、すべての必須環境変数を含めた
- [ ] デプロイ後に環境変数が正しく設定されているか確認した

> AI への指示：
> 「Cloud Runに環境変数を追加する場合は、必ず `--update-env-vars` を使用してください。
> `--set-env-vars` を使う場合は、現在の環境変数を確認し、すべての必須環境変数を含めてください。
> デプロイ後は必ず環境変数が正しく設定されているか確認してください。」

### 15.3 ログ監視

**Cloud Logging クエリ**:
```
resource.type="cloud_run_revision"
resource.labels.service_name="api-key-server"
severity>=ERROR
```

**重要な監視指標**:
- エラー率（HTTP 5xx）
- レート制限超過率（HTTP 429）
- 認証失敗率（HTTP 401）
- 平均レスポンス時間
- 上流APIエラー率（HTTP 502）

---

## 16. 実装後の品質チェックフロー（必須）

### 16.1 品質チェックの流れ

実装完了後、以下の順序で品質チェックを実施してください：

```
1. 実装完了
2. ビルドエラーチェック
3. リンタエラーチェック
4. エラー修正
5. ソースレビュー実施
6. レビュー指摘対応
7. 完了
```

### 16.2 ビルドエラーチェック

**Pythonコンパイルチェック**:
```bash
# 全ファイルのシンタックスチェック
python -m py_compile app/*.py
python -m py_compile app/providers/*.py
```

**Dockerビルドチェック**:
```bash
# コンテナイメージのビルド確認
docker build -t api-key-server:test .
```

### 16.3 リンタエラーチェック

このプロジェクトではリンタツールは導入していませんが、以下のチェックを手動で実施してください：

**基本チェック項目**:
- [ ] 未使用のインポート文がないか
- [ ] 未使用の変数がないか
- [ ] 型ヒントが適切に付与されているか
- [ ] docstringが記述されているか（公開関数・クラス）
- [ ] 命名規則に従っているか（PEP 8準拠）

**推奨リンタツール（導入検討）**:
```bash
# flake8（コーディング規約チェック）
pip install flake8
flake8 app/ --max-line-length=120

# mypy（型チェック）
pip install mypy
mypy app/ --ignore-missing-imports

# black（フォーマッター）
pip install black
black app/ --check
```

### 16.4 ソースレビュー実施

ビルドエラーとリンタエラーを解消した後、以下のレビュー計画に基づいてソースレビューを実施してください。

**レビュー計画書の作成**:
```
「実装が完了したので、コードレビュー計画書を作成してください。」
```

**フェーズ別レビュー実施**:
1. Phase 1: Critical（認証・セキュリティ関連）
2. Phase 2: High（コア機能・設定管理）
3. Phase 3: Medium（パフォーマンス・運用）

**レビュー観点**:
- セキュリティ脆弱性（認証バイパス、情報漏洩、APIキー露出）
- パフォーマンス問題（N+1問題、メモリリーク、非効率なクエリ）
- コード品質（単一責任原則、DRY原則、命名規則）
- エラーハンドリング（例外の適切な捕捉、ログレベル、リトライロジック）

**レビュー結果の対応**:
- Critical: 即座に修正（デプロイブロッカー）
- Major: 修正推奨（次回リリース前に対応）
- Minor: 改善提案（時間があれば対応）
- Info: 参考情報（ドキュメント化のみ）

> AI への指示例：
> 「実装が完了したので、ビルドエラーとリンタエラーをチェックしてください。
> エラーがなければ、コードレビュー計画書を作成してレビューを実施してください。」

---

## 17. AI への依頼テンプレート（このプロジェクト専用）

このプロジェクトで AI にコードを書いてもらうときの依頼例：

```text
あなたは Python/FastAPI を使用した API Key Proxy Server の
開発アシスタントです。

共通ルール（~/.claude/AI_COMMON_RULES.md）に加えて、
次のプロジェクト固有ルールを守ってください：
- 非同期処理（async/await）を使用
- Pydantic でデータ検証
- 上流APIのエラー詳細は隠蔽（502 Bad Gateway）
- APIキーやシークレットは絶対にログに出力しない
- すべてのプロバイダのレスポンスはOpenAI互換形式に統一

【依頼内容】
新しいAIプロバイダ（Cohere）を追加してください。
- app/providers/cohere.py を作成
- call() メソッドを実装
- リクエスト・レスポンスのフォーマット変換を実装
- app/upstream.py に登録

実装後は以下の流れで品質チェックを実施してください：
1. ビルドエラーチェック（python -m py_compile）
2. リンタチェック（未使用変数、型ヒント、命名規則）
3. エラー修正
4. ソースレビュー実施
```

---

## 18. 更新ポリシー

- この専用メモリは、**機能追加・仕様変更時に更新**する
- 大きな方針変更がある場合は、共通ルール側も確認する
- 更新履歴や改訂日を下に追記して管理する

---

### 更新履歴

- 2025-01-15: 初版作成（コードベース探索結果を基に作成）
- 2025-12-15: セクション16「実装後の品質チェックフロー」を追加
  - ビルドエラーチェック → リンタチェック → ソースレビューの流れを明確化
  - レビュー観点とレビュー結果の対応方針を追加
