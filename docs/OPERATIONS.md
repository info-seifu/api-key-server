# 運用手順書（API Key Proxy Server）

> このファイルは運用作業時に参照する手順書です。
> 開発ルールは `.claude/CLAUDE.md` を参照してください。

---

## 1. ローカル開発環境セットアップ

### 必要条件

| ツール | バージョン | 備考 |
|--------|-----------|------|
| Python | 3.11 | pyenv推奨 |
| Docker | 最新 | オプション（コンテナビルド時） |
| Google Cloud SDK | 最新 | Secret Manager使用時のみ |

### セットアップ手順

```bash
# 1. リポジトリクローン
git clone <repository-url>
cd api-key-server

# 2. 仮想環境作成・有効化
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. 依存関係インストール
pip install -r requirements.txt

# 4. ローカル設定ファイル作成
mkdir -p config
cat > config/openai_api_keys.json << 'EOF'
{
  "product-dev": {
    "providers": {
      "openai": {
        "api_key": "sk-your-api-key",
        "models": ["gpt-4o", "gpt-4o-mini"]
      }
    }
  }
}
EOF

# 5. 環境変数設定（Secret Manager不使用）
export USE_SECRET_MANAGER=false
export API_KEY_SERVER_MAX_TOKENS=8192
export API_KEY_SERVER_REQUEST_TIMEOUT_SECONDS=90

# 6. サーバー起動
uvicorn app.main:app --reload --port 8000
```

### 動作確認

```bash
# ヘルスチェック
curl http://localhost:8000/health

# チャットエンドポイント
curl -X POST http://localhost:8000/v1/chat/product-dev \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <your-jwt-token>" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

### Secret Manager使用時の追加設定

```bash
# GCPプロジェクト認証
gcloud auth application-default login

# 環境変数設定
export USE_SECRET_MANAGER=true
export API_KEY_SERVER_GCP_PROJECT_ID=your-project-id
```

---

## 2. CI/CDパイプライン

### Cloud Build トリガー設定

| 項目 | 値 |
|------|-----|
| トリガー条件 | mainブランチへのpush |
| ビルドファイル | `cloudbuild.yaml` |
| デプロイ先 | Cloud Run (asia-northeast1) |
| 認証 | IAP認証（--no-allow-unauthenticated） |

### パイプライン処理フロー

```
1. mainブランチへpush
2. Cloud Buildトリガー起動
3. Dockerイメージビルド (gcr.io/$PROJECT_ID/api-key-server)
4. Container Registryへpush
5. Cloud Runへデプロイ
6. 完了通知
```

### 手動トリガー実行

```bash
# トリガーID確認
gcloud builds triggers list --project=your-project-id

# 手動実行
gcloud builds triggers run <trigger-id> \
  --branch=main \
  --project=your-project-id

# ビルド状況確認
gcloud builds list --limit=5 --project=your-project-id

# ビルドログ確認
gcloud builds log <build-id> --project=your-project-id
```

---

## 3. 本番デプロイ

### Cloud Run デプロイコマンド

```bash
# Dockerイメージビルド
docker build -t gcr.io/your-project-id/api-key-server:latest .

# イメージプッシュ
docker push gcr.io/your-project-id/api-key-server:latest

# Cloud Runデプロイ（すべての必須環境変数を含める）
gcloud run deploy api-key-server \
  --image gcr.io/${PROJECT_ID}/api-key-server \
  --region asia-northeast1 \
  --set-env-vars "USE_SECRET_MANAGER=true,API_KEY_SERVER_GCP_PROJECT_ID=${PROJECT_ID},API_KEY_SERVER_MAX_TOKENS=8192,API_KEY_SERVER_REQUEST_TIMEOUT_SECONDS=240" \
  --timeout=300 \
  --no-allow-unauthenticated
```

### ⚠️ 環境変数の管理ルール

`--set-env-vars` は**既存の環境変数を完全に上書き**します。

| オプション | 動作 | 使用場面 |
|-----------|------|----------|
| `--set-env-vars=A=1,B=2` | すべて上書き | 環境変数を完全に置き換える時 |
| `--update-env-vars=C=3` | 追加/更新のみ | **新しい変数を追加する時（推奨）** |
| `--remove-env-vars=D` | 指定変数のみ削除 | 特定の変数を削除する時 |

### 環境変数の確認・更新

```bash
# 現在の環境変数を確認
gcloud run services describe api-key-server \
  --region=asia-northeast1 \
  --format="yaml(spec.template.spec.containers[0].env)" \
  --project=your-project-id

# 変数を追加（既存は保持）
gcloud run services update api-key-server \
  --region=asia-northeast1 \
  --update-env-vars API_KEY_SERVER_MAX_TOKENS=8192 \
  --project=your-project-id
```

### 必須環境変数一覧

| 変数名 | 値 | 説明 |
|--------|-----|------|
| `USE_SECRET_MANAGER` | `true` | Secret Manager有効化 |
| `API_KEY_SERVER_GCP_PROJECT_ID` | プロジェクトID | GCPプロジェクト |
| `API_KEY_SERVER_MAX_TOKENS` | `8192` | トークン上限 |
| `API_KEY_SERVER_REQUEST_TIMEOUT_SECONDS` | `240` | タイムアウト |

---

## 4. Secret Manager メンテナンス

### シークレット構成

| シークレット名 | 形式 | 内容 |
|--------------|------|------|
| `openai-api-keys` | JSON | プロダクト設定 |
| `jwt-public-keys` | JSON | JWT公開鍵リスト |
| `hmac-secrets` | JSON | HMACシークレットリスト |

### シークレット更新

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

### シークレット確認

```bash
# 最新バージョンの確認
gcloud secrets versions list openai-api-keys --project=your-project-id

# シークレットの読み取り
gcloud secrets versions access latest \
  --secret=openai-api-keys \
  --project=your-project-id
```

---

## 5. ログ監視

### Cloud Logging クエリ

```
resource.type="cloud_run_revision"
resource.labels.service_name="api-key-server"
severity>=ERROR
```

### 重要な監視指標

| 指標 | 説明 |
|------|------|
| HTTP 5xx率 | サーバーエラー |
| HTTP 429率 | レート制限超過 |
| HTTP 401率 | 認証失敗 |
| HTTP 502率 | 上流APIエラー |
| 平均レスポンス時間 | パフォーマンス |

### ログ取得コマンド

```bash
# 最新ログ取得
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="api-key-server"' \
  --limit=50 \
  --project=your-project-id

# エラーログのみ
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="api-key-server" AND severity>=ERROR' \
  --limit=20 \
  --project=your-project-id

# リアルタイム監視
gcloud logging tail \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="api-key-server"' \
  --project=your-project-id
```

---

## 6. トラブルシューティング

### よくあるエラーと対処法

| エラー | HTTPステータス | 原因 | 対処法 |
|--------|---------------|------|--------|
| `Upstream service error` | 502 | 上流APIエラー、APIキー無効 | Secret Managerのキーを確認 |
| `Unauthorized` | 401 | JWT/HMAC認証失敗 | トークン有効期限、署名形式確認 |
| `Rate limit exceeded` | 429 | レート制限超過 | 待機後リトライ |
| `Product not found` | 404 | プロダクト未登録 | シークレット設定確認 |
| `Model not supported` | 400 | モデル名不一致 | modelsリスト確認 |
| `Streaming not supported` | 400 | ストリーミング要求 | `stream: false`に変更 |

### 環境変数関連のトラブル

**症状**: デプロイ後にサービスが動作しない

**確認方法**:
```bash
gcloud run services describe api-key-server \
  --region=asia-northeast1 \
  --format="yaml(spec.template.spec.containers[0].env)" \
  --project=your-project-id
```

**対処法**:
```bash
# 必須環境変数をすべて含めて再設定
gcloud run services update api-key-server \
  --region=asia-northeast1 \
  --set-env-vars "USE_SECRET_MANAGER=true,API_KEY_SERVER_GCP_PROJECT_ID=your-project-id,API_KEY_SERVER_MAX_TOKENS=8192,API_KEY_SERVER_REQUEST_TIMEOUT_SECONDS=240" \
  --project=your-project-id
```

### Secret Manager関連のトラブル

**症状**: `Failed to load secrets from Secret Manager`

**確認手順**:
```bash
# シークレット一覧確認
gcloud secrets list --project=your-project-id

# シークレット内容確認
gcloud secrets versions access latest \
  --secret=openai-api-keys \
  --project=your-project-id

# サービスアカウントの権限確認
gcloud projects get-iam-policy your-project-id \
  --flatten="bindings[].members" \
  --filter="bindings.role:roles/secretmanager.secretAccessor"
```

**対処法**: サービスアカウントに`Secret Manager Secret Accessor`ロールを付与

---

## 更新履歴

- 2026-01-09: 初版作成（CLAUDE.mdから分離）
