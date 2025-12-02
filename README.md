# API Key Proxy Server

A minimal FastAPI proxy designed for Cloud Run. It keeps upstream API keys on the server side, enforces short-lived client authentication (JWT or HMAC), applies per-product/user rate limits, and forwards allowed chat completion calls to upstream providers like OpenAI.

## Features
- JWT (RS256) or HMAC + timestamp authentication.
- Per-product/user token-bucket QPS control and a daily quota guard. Redis-backed when available, with in-memory fallback that also enforces daily quotas.
- Parameter validation for allowed models, `max_tokens`, and temperature range.
- Upstream request errors are surfaced as 502 responses rather than generic failures.
- Simple `/healthz` endpoint for Cloud Run health checks.
- Dockerfile tailored for Cloud Run and configurable via environment variables.

## Configuration
All settings are loaded from environment variables with the prefix `API_KEY_SERVER_`.

### Basic Configuration

| Variable | Description | Example |
| --- | --- | --- |
| `API_KEY_SERVER_PRODUCT_KEYS` | JSON map of product IDs to upstream API keys. | `{ "product-a": "sk-..." }` |
| `API_KEY_SERVER_JWT_PUBLIC_KEYS` | JSON map of `kid` to RSA public keys for JWT validation. | `{ "kid-1": "-----BEGIN PUBLIC KEY-----..." }` |
| `API_KEY_SERVER_CLIENT_HMAC_SECRETS` | JSON map of client IDs to HMAC secrets. | `{ "desktop": "shared-secret" }` |
| `API_KEY_SERVER_ALLOWED_MODELS` | JSON list of allowed models. | `["gpt-4o", "gpt-4o-mini"]` |
| `API_KEY_SERVER_MAX_TOKENS` | Upper bound for `max_tokens`. | `2048` |
| `API_KEY_SERVER_OPENAI_BASE_URL` | Upstream chat/completions endpoint. | `https://api.openai.com/v1/chat/completions` |
| `API_KEY_SERVER_REDIS_URL` | Redis URL for rate limiting (optional). | `redis://:pass@host:6379/0` |
| `API_KEY_SERVER_REDIS_PREFIX` | Prefix for Redis keys. | `api-key-server` |
| `API_KEY_SERVER_HMAC_CLOCK_TOLERANCE_SECONDS` | Allowed clock skew for timestamped HMAC. | `300` |

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
```bash
# Create product keys secret
cat > product-keys.json <<EOF
{
  "product-a": "sk-xxxxxxxxxxxxx",
  "product-b": "sk-yyyyyyyyyyy"
}
EOF

gcloud secrets create openai-api-keys \
  --data-file=product-keys.json \
  --replication-policy=automatic

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
  --set-env-vars "USE_SECRET_MANAGER=true" \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT_ID}" \
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

### GitHub からの自動デプロイを設定する場合の例 (Cloud Build トリガー)

#### Option 1: Using Secret Manager (Recommended)
1. GCP プロジェクトで Cloud Build と Secret Manager を有効化し、GitHub 連携を設定する。
2. 上記の手順でSecret Managerにシークレットを作成する。
3. Cloud Build トリガーを作成し、`main` ブランチへの push をトリガー条件にする。
4. トリガーのビルドステップ例 (`cloudbuild.yaml`):
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
         - 'USE_SECRET_MANAGER=true,GOOGLE_CLOUD_PROJECT=$PROJECT_ID'
         - '--allow-unauthenticated=false'
   ```
5. Cloud Runのサービスアカウントに Secret Manager Secret Accessor 権限が付与されていることを確認する。

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
