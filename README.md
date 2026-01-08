# API Key Proxy Server

A minimal FastAPI proxy designed for Cloud Run. It keeps upstream API keys on the server side, enforces short-lived client authentication (JWT, HMAC, or IAP), applies per-product/user rate limits, and forwards allowed chat completion calls to upstream providers like OpenAI, Gemini, and Anthropic.

## Features
- JWT (RS256), HMAC + timestamp, or Google IAP authentication.
- Multi-provider support: OpenAI, Google Gemini, Anthropic Claude with automatic provider selection based on model name.
- Per-product/user token-bucket QPS control and a daily quota guard. Redis-backed when available, with in-memory fallback that also enforces daily quotas.
- Parameter validation for allowed models, `max_tokens`, and temperature range.
- Upstream request errors are surfaced as 502 responses rather than generic failures.
- Simple `/healthz` endpoint for Cloud Run health checks.
- Dockerfile tailored for Cloud Run and configurable via environment variables.

---

## For Client Developers（クライアント開発者向け）

このプロキシサーバーを使用してAI APIにアクセスする方法を説明します。

### エンドポイント

```
ベースURL: https://your-proxy-server.run.app
チャット補完: POST /chat/{product_id}
```

### 利用可能なモデル

| プロバイダー | モデル名 | 用途 |
| --- | --- | --- |
| OpenAI | `gpt-4o` | テキスト生成（高性能） |
| OpenAI | `gpt-4o-mini` | テキスト生成（高速・低コスト） |
| Google Gemini | `gemini-3-pro-preview-11-2025` | テキスト生成（最新） |
| Google Gemini | `gemini-2.5-pro-preview-tts` | 音声生成（TTS） |
| Google Gemini | `gemini-2.0-flash-exp` | テキスト生成（高速） |
| Anthropic Claude | `claude-3-7-sonnet-20250219` | テキスト生成（推論特化） |

### リクエスト形式（OpenAI互換）

```json
POST /chat/{product_id}
Content-Type: application/json

{
  "model": "gpt-4o",
  "messages": [
    {"role": "user", "content": "こんにちは"}
  ]
}
```

### レスポンス形式（OpenAI互換）

```json
{
  "id": "chatcmpl-xxxxx",
  "object": "chat.completion",
  "model": "gpt-4o",
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "こんにちは！何かお手伝いできることはありますか？"
    },
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 20,
    "total_tokens": 30
  }
}
```

### 認証

**IAP経由でアクセスする場合（推奨）:**
- Google Workspaceでログイン済みであれば、特別な認証ヘッダーは不要
- IAPが自動的にJWTトークンを付与します

**開発環境など直接アクセスする場合:**
- JWT または HMAC 認証が必要（別途管理者に問い合わせ）

### サンプルコード

#### Python（requests）

```python
import requests

response = requests.post(
    "https://your-proxy-server.run.app/chat/product-a",
    json={
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "こんにちは"}]
    }
)

result = response.json()
print(result["choices"][0]["message"]["content"])
```

#### Python（OpenAI SDK）

```python
from openai import OpenAI

client = OpenAI(
    api_key="dummy",  # ダミーでOK（プロキシがAPIキーを管理）
    base_url="https://your-proxy-server.run.app/chat/product-a"
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "こんにちは"}]
)

print(response.choices[0].message.content)
```

#### JavaScript/TypeScript

```javascript
const response = await fetch(
  "https://your-proxy-server.run.app/chat/product-a",
  {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      model: "gpt-4o",
      messages: [{role: "user", content: "こんにちは"}]
    })
  }
);

const result = await response.json();
console.log(result.choices[0].message.content);
```

### エラーハンドリング

| HTTPステータス | 意味 | 対処方法 |
| --- | --- | --- |
| 200 | 成功 | - |
| 400 | リクエストエラー | リクエストパラメータを確認 |
| 401 | 認証エラー | IAPでログインしているか確認 |
| 404 | プロダクトが見つからない | product_idを確認 |
| 429 | レート制限超過 | 少し待ってから再試行 |
| 502 | 上流APIエラー | OpenAI/Gemini/Anthropic側のエラー |

### 重要な注意事項

- ⚠️ **APIキーは不要です**。プロキシサーバーが管理します。
- ⚠️ **APIキーをクライアント側に保存しないでください**。セキュリティリスクがあります。
- ✅ モデル名を変更するだけで、異なるプロバイダー（OpenAI/Gemini/Anthropic）を使い分けられます。
- ✅ すべてのレスポンスはOpenAI互換形式で返されます。

---

## Configuration
All settings are loaded from environment variables with the prefix `API_KEY_SERVER_`.

### Basic Configuration

| Variable | Description | Example |
| --- | --- | --- |
| `API_KEY_SERVER_PRODUCT_KEYS` | **(Legacy)** JSON map of product IDs to OpenAI API keys. For backward compatibility. | `{ "product-a": "sk-..." }` |
| `API_KEY_SERVER_PRODUCT_CONFIGS` | **(Recommended)** JSON map of product IDs to multi-provider configurations. See [Multi-Provider Configuration](#multi-provider-configuration). | See below |
| `API_KEY_SERVER_JWT_PUBLIC_KEYS` | JSON map of `kid` to RSA public keys for JWT validation. | `{ "kid-1": "-----BEGIN PUBLIC KEY-----..." }` |
| `API_KEY_SERVER_CLIENT_HMAC_SECRETS` | JSON map of client IDs to HMAC secrets. | `{ "desktop": "shared-secret" }` |
| `API_KEY_SERVER_ALLOWED_MODELS` | JSON list of allowed models. | `["gpt-4o", "gpt-4o-mini"]` |
| `API_KEY_SERVER_MAX_TOKENS` | Upper bound for `max_tokens`. | `2048` |
| `API_KEY_SERVER_OPENAI_BASE_URL` | **(Legacy)** Upstream chat/completions endpoint for OpenAI. | `https://api.openai.com/v1/chat/completions` |
| `API_KEY_SERVER_REDIS_URL` | Redis URL for rate limiting (optional). | `redis://:pass@host:6379/0` |
| `API_KEY_SERVER_REDIS_PREFIX` | Prefix for Redis keys. | `api-key-server` |
| `API_KEY_SERVER_HMAC_CLOCK_TOLERANCE_SECONDS` | Allowed clock skew for timestamped HMAC. | `300` |

### Multi-Provider Configuration

The server supports configuring multiple AI providers per product. Each provider can have its own API key and supported models list.

**Configuration Format:**
```json
{
  "product-a": {
    "providers": {
      "openai": {
        "api_key": "sk-proj-xxxxxxxxxxxxx",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"]
      },
      "gemini": {
        "api_key": "AIzaSyXXXXXXXXXXXXXXXXXXXXX",
        "models": ["gemini-1.5-pro", "gemini-1.5-flash"]
      },
      "anthropic": {
        "api_key": "sk-ant-api03-XXXXXXXXXXXXXXX",
        "models": ["claude-3-5-sonnet-20241022", "claude-3-opus-20240229"]
      }
    }
  }
}
```

**How it works:**
- The server automatically selects the appropriate provider based on the model name in the request
- If `models` list is specified, it matches the requested model against each provider's supported models
- If `models` list is empty or not specified for a provider, that provider accepts any model name
- All responses are converted to OpenAI-compatible format regardless of the upstream provider

**Supported Providers:**
- **OpenAI**: Supports all OpenAI models (gpt-4o, gpt-4o-mini, gpt-3.5-turbo, etc.)
- **Gemini**: Supports Google Gemini models (gemini-1.5-pro, gemini-1.5-flash, etc.)
- **Anthropic**: Supports Claude models (claude-3-5-sonnet-20241022, claude-3-opus-20240229, etc.)

**Optional `base_url` parameter:**
You can specify a custom endpoint URL for each provider:
```json
{
  "product-a": {
    "providers": {
      "openai": {
        "api_key": "sk-proj-xxxxxxxxxxxxx",
        "base_url": "https://custom-openai-proxy.example.com/v1/chat/completions",
        "models": ["gpt-4o"]
      }
    }
  }
}
```

**Backward Compatibility:**
The server still supports the legacy `PRODUCT_KEYS` format for OpenAI-only configurations. If both formats are present, the new multi-provider format takes precedence.

### Secret Manager Integration (Recommended for Production)

For production deployments, it's recommended to use Google Cloud Secret Manager to store sensitive data instead of passing secrets as environment variables.

| Variable | Description | Example |
| --- | --- | --- |
| `USE_SECRET_MANAGER` | Enable Secret Manager integration. Set to `true`, `1`, or `yes`. | `true` |
| `GCP_PROJECT` or `GOOGLE_CLOUD_PROJECT` | GCP project ID for Secret Manager. | `my-project-123` |
| `API_KEY_SERVER_GCP_PROJECT_ID` | Alternative way to specify GCP project ID. | `my-project-123` |
| `API_KEY_SERVER_SECRET_PRODUCT_KEYS_NAME` | Secret name for product keys. | `openai-api-keys` (default) |
| `API_KEY_SERVER_SECRET_JWT_KEYS_NAME` | Secret name for JWT public keys. | `jwt-public-keys` (default) |
| `API_KEY_SERVER_SECRET_HMAC_SECRETS_NAME` | Secret name for HMAC secrets. | `hmac-secrets` (default) |

**How it works:**
- When `USE_SECRET_MANAGER=true`, the server automatically loads secrets from Secret Manager at startup
- If environment variables (`API_KEY_SERVER_PRODUCT_KEYS`, etc.) are not set, it fetches from Secret Manager
- Environment variables take precedence over Secret Manager (useful for local development)

### Required Environment Variables for Cloud Run Deployment

以下の環境変数は Cloud Run デプロイ時に必須です。**`--set-env-vars`は既存の環境変数をクリアする**ため、再デプロイ時はすべての必要な変数を含めてください。

| 変数名 | 必須 | デフォルト値 | 説明 |
|--------|------|-------------|------|
| `USE_SECRET_MANAGER` | ○ | - | Secret Manager使用フラグ (`true`で有効化) |
| `API_KEY_SERVER_GCP_PROJECT_ID` | ○ | - | GCPプロジェクトID（Secret Manager接続に使用） |
| `API_KEY_SERVER_MAX_TOKENS` | - | 8192 | 最大トークン数（1-200000） |
| `API_KEY_SERVER_REQUEST_TIMEOUT_SECONDS` | - | 90 | APIリクエストタイムアウト（秒）<br>長いレスポンスの場合は240推奨 |
| `API_KEY_SERVER_SECRET_PRODUCT_KEYS_NAME` | - | openai-api-keys | Product Keys用シークレット名 |
| `API_KEY_SERVER_SECRET_JWT_KEYS_NAME` | - | jwt-public-keys | JWT公開鍵用シークレット名 |
| `API_KEY_SERVER_SECRET_HMAC_SECRETS_NAME` | - | hmac-secrets | HMAC秘密鍵用シークレット名 |

**⚠️ 重要**: デプロイ時は必ず README.md に記載されたコマンドを使用してください。`--set-env-vars`で一部の変数のみ指定すると、他の重要な環境変数が削除されます。

Rate limit defaults (per product/user):
- Bucket capacity: 10 requests
- Refill: 5 tokens/sec
- Daily quota: 200,000 requests

## Run locally
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8080
```

## Deploy to Cloud Run

### Option 1: Using Secret Manager (Recommended)

**Step 1: Create secrets in Secret Manager**

**Option A: Multi-Provider Configuration (Recommended)**
```bash
# Create product configs secret with multiple providers
cat > product-keys.json <<EOF
{
  "product-a": {
    "providers": {
      "openai": {
        "api_key": "sk-proj-xxxxxxxxxxxxx",
        "models": ["gpt-4o", "gpt-4o-mini"]
      },
      "gemini": {
        "api_key": "AIzaSyXXXXXXXXXXXXXXXXXXXXX",
        "models": ["gemini-1.5-pro", "gemini-1.5-flash"]
      },
      "anthropic": {
        "api_key": "sk-ant-api03-XXXXXXXXXXXXXXX",
        "models": ["claude-3-5-sonnet-20241022"]
      }
    }
  }
}
EOF

gcloud secrets create openai-api-keys \
  --data-file=product-keys.json \
  --replication-policy=automatic
```

**Option B: Legacy Single-Provider Configuration (OpenAI only)**
```bash
# Create product keys secret (legacy format)
cat > product-keys.json <<EOF
{
  "product-a": "sk-xxxxxxxxxxxxx",
  "product-b": "sk-yyyyyyyyyyy"
}
EOF

gcloud secrets create openai-api-keys \
  --data-file=product-keys.json \
  --replication-policy=automatic
```

# Create JWT public keys secret
cat > jwt-keys.json <<EOF
{
  "kid-1": "-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhki...\n-----END PUBLIC KEY-----"
}
EOF

gcloud secrets create jwt-public-keys \
  --data-file=jwt-keys.json \
  --replication-policy=automatic

# Create HMAC secrets
cat > hmac-secrets.json <<EOF
{
  "desktop": "shared-secret-123",
  "mobile": "shared-secret-456"
}
EOF

gcloud secrets create hmac-secrets \
  --data-file=hmac-secrets.json \
  --replication-policy=automatic

# Clean up local files
rm product-keys.json jwt-keys.json hmac-secrets.json
```

**Step 2: Grant Secret Manager access to Cloud Run service account**
```bash
# Get the Cloud Run service account email (after first deploy, or use default compute SA)
PROJECT_ID=$(gcloud config get-value project)
SERVICE_ACCOUNT="${PROJECT_ID}-compute@developer.gserviceaccount.com"

# Grant Secret Manager Secret Accessor role
gcloud secrets add-iam-policy-binding openai-api-keys \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding jwt-public-keys \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding hmac-secrets \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/secretmanager.secretAccessor"
```

**Step 3: Build and deploy to Cloud Run**
```bash
PROJECT_ID=$(gcloud config get-value project)

# Build image
gcloud builds submit --tag gcr.io/${PROJECT_ID}/api-key-server

# Deploy with Secret Manager integration
gcloud run deploy api-key-server \
  --image gcr.io/${PROJECT_ID}/api-key-server \
  --region asia-northeast1 \
  --set-env-vars "USE_SECRET_MANAGER=true,API_KEY_SERVER_GCP_PROJECT_ID=${PROJECT_ID},API_KEY_SERVER_MAX_TOKENS=8192,API_KEY_SERVER_REQUEST_TIMEOUT_SECONDS=240" \
  --timeout=300 \
  --allow-unauthenticated=false
```

### Option 2: Using Environment Variables (Not Recommended for Production)
```bash
gcloud builds submit --tag gcr.io/PROJECT_ID/api-key-server
gcloud run deploy api-key-server \
  --image gcr.io/PROJECT_ID/api-key-server \
  --region asia-northeast1 \
  --set-env-vars "API_KEY_SERVER_PRODUCT_KEYS={\\\"product-a\\\":\\\"<secret>\\\"}" \
  --set-env-vars "API_KEY_SERVER_JWT_PUBLIC_KEYS={\\\"kid-1\\\":\\\"-----BEGIN PUBLIC KEY-----...\\\"}" \
  --allow-unauthenticated=false
```

### 自動デプロイの設定（推奨）

**⚠️ 重要**: 環境変数削除のリスクを防ぐため、自動デプロイを強く推奨します。

このリポジトリには `cloudbuild.yaml` が含まれており、すべての必須環境変数が定義されています。

#### 手順:

1. **GCP プロジェクトで Cloud Build を有効化**
   ```bash
   gcloud services enable cloudbuild.googleapis.com --project=interview-api-472500
   ```

2. **GitHub 連携を設定**（初回のみ）
   - [GCP Console > Cloud Build > トリガー](https://console.cloud.google.com/cloud-build/triggers)
   - 「トリガーを作成」をクリック
   - 「リポジトリを選択」で GitHub を選択し、`info-seifu/api-key-server` を接続

3. **Cloud Build トリガーを作成**
   - **名前**: `api-key-server-deploy`
   - **イベント**: ブランチにpush
   - **ソース（リポジトリ）**: `info-seifu/api-key-server`
   - **ブランチ**: `^main$`
   - **構成**: Cloud Build 構成ファイル（yaml または json）
   - **Cloud Build 構成ファイルの場所**: `/cloudbuild.yaml`
   - 「作成」をクリック

4. **動作確認**
   - `main` ブランチに何かpushすると、自動的にビルド・デプロイが実行されます
   - [Cloud Build の履歴](https://console.cloud.google.com/cloud-build/builds)で進捗を確認できます

#### cloudbuild.yaml の内容

このファイルには以下の環境変数が固定されています（手動デプロイ時の削除リスクを防止）：
- `USE_SECRET_MANAGER=true`
- `API_KEY_SERVER_GCP_PROJECT_ID=$PROJECT_ID`
- `API_KEY_SERVER_MAX_TOKENS=8192`
- `API_KEY_SERVER_REQUEST_TIMEOUT_SECONDS=240`

環境変数を変更する場合は、`cloudbuild.yaml` を編集してコミットすることで、変更履歴が Git で管理されます。

#### Option 2: Using Environment Variables (Not Recommended)
1. GCP プロジェクトで Cloud Build を有効化し、GitHub 連携を設定する。
2. Cloud Build トリガーを作成し、`main` ブランチへの push をトリガー条件にする。
3. トリガーのビルドステップ例:
   ```yaml
   steps:
     - name: 'gcr.io/cloud-builders/gcloud'
       args: ['builds', 'submit', '--tag', 'gcr.io/$PROJECT_ID/api-key-server']
     - name: 'gcr.io/cloud-builders/gcloud'
       args:
         - 'run'
         - 'deploy'
         - 'api-key-server'
         - '--image'
         - 'gcr.io/$PROJECT_ID/api-key-server'
         - '--region'
         - 'asia-northeast1'
         - '--set-env-vars'
         - 'API_KEY_SERVER_PRODUCT_KEYS={"product-a":"<secret>"}'
         - '--set-env-vars'
         - 'API_KEY_SERVER_JWT_PUBLIC_KEYS={"kid-1":"-----BEGIN PUBLIC KEY-----..."}'
         - '--allow-unauthenticated=false'
   ```
4. 環境変数は Cloud Build トリガーの **シークレット/変数設定** で管理し、上記の `--set-env-vars` に差し込む（JSON を含むため引用符のエスケープに注意）。
5. 必要に応じて `API_KEY_SERVER_ALLOWED_MODELS` や Redis 設定なども `--set-env-vars` で追加する。

See `docs/cloud-run-internal-design.md` for the security architecture that guided this implementation.

## Google Workspace 認証フローとの関係
- このサーバーは **すでに認証済みのクライアント** からのリクエストを前提にしており、Google Workspace/IAP でのログインやドメインチェックそのものは実装していません。Workspace 認証を使う場合は、IAP や Identity-Aware Proxy の前段でログインを完了させた上で、このサーバーに到達するリクエストに JWT または HMAC ヘッダーを付与してください。
- 上流 API キーは `API_KEY_SERVER_PRODUCT_KEYS` でコンテナ起動時に注入し、リクエスト転送時にヘッダーへ設定するだけなので **ファイル保存は行わずメモリ上でのみ使用** されます。
- クライアントへ直接 API キーを返すエンドポイントを設ける方法と比べると、本プロキシ方式はキーの配布・漏洩リスクを減らし、レートリミットやプロダクト単位のポリシーを一元化できるため、社内限定環境ではプロキシ経由を推奨します。
