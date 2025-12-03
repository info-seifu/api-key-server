# Cloud Run を社内専用に閉じるプロキシ設計書

最終更新: 2025-12-03 (Asia/Tokyo)

本書は、OpenAI、Google Gemini、Anthropic Claude 等の外部APIキーをサーバ側のみで保持しながら、**Cloud Run を「社内からのみ」利用可能なプロキシ**として運用するための設計を示す。運用コストを抑えつつ、鍵の悪用や第三者の不正利用を防ぐことを目的とする。

---

## 1. 目的・要件

### 1.1 目的
- クライアント配布アプリ（Windows等）から直接外部APIに触れさせず、**Cloud Run のプロキシ経由**で呼び出す。
- **社内アクセスのみ**許容し、社外からの到達・濫用を防ぐ。
- **プロダクト単位で複数のAIプロバイダー（OpenAI、Gemini、Anthropic等）**を設定し、鍵の影響範囲を最小化。
- モデル名に基づいて自動的に適切なプロバイダーを選択し、レスポンスをOpenAI互換形式で返却。

### 1.2 機能要件
- Chat/Completions 等の少数APIパスを提供（最小限）。
- **短寿命トークン（JWTまたはHMAC+Timestamp）**によるクライアント認証。
- **ユーザー/プロダクト単位のレート制限**（QPS + 日次上限）。
- **監査ログ**（誰が/どのモデル/使用量）と異常時の自動遮断。

### 1.3 非機能要件
- 低コスト（アイドル時ゼロスケール）。
- 運用省力化（マネージド重視）。
- セキュリティベストプラクティス準拠（Secrets、WAF、最小権限）。

---

## 2. 全体アーキテクチャ

```
[Client (社内PC/VM)]
     │ HTTPS (OpenAI互換リクエスト + モデル名指定)
     ▼
[HTTPS LB + Cloud Armor (WAF/Rate rules/IP許可)]
     │ IAP/ID Token or Authenticated invocations
     ▼
[Cloud Run (FastAPI Proxy)]
     │ ├─ Secret Manager (マルチプロバイダー設定)  [読み取りのみ]
     │ ├─ Memorystore / Redis (レート制限/トークン管理)
     │ ├─ Provider Adapters (OpenAI/Gemini/Anthropic)
     │ └─ Cloud Logging/Monitoring (監査・アラート)
     ▼
[External API (OpenAI / Gemini / Anthropic)]
     ▼
[OpenAI互換形式のレスポンスに統一して返却]
```

- **入口**は LB + **Cloud Armor**。IAP もしくは Cloud Run の**認証必須**で保護。
- **アプリ内でも**短寿命トークン検証（JWT/HMAC/IAP JWT）を実施（二重ロック）。
- **Secret Manager**で複数プロバイダーの鍵を保管。**ログはマスク**。
- **Redis**でレート制限/日次上限を実装。
- **プロバイダーアダプター**がモデル名に基づいて適切なプロバイダーを選択し、リクエスト/レスポンス形式を変換。

---

## 3. ネットワーク & アクセス制御方針

### 3.1 Ingress/認証
- Cloud Run サービス: **認証必須**に設定。
- IAP or ID Token による**アイデンティティベース**のガードを実施。
- サービス アカウント/Google Workspace グループで **社内ユーザーのみ**許可。

### 3.2 IP許可（任意だが強く推奨）
- 会社拠点や社内VPNの**出口IPを許可リスト**に追加（Cloud Armor の allowlist）。
- 物理的拠点・VPN変更に伴いルールを更新。

### 3.3 WAF（Cloud Armor）
- 既定の OWASP ルール + Bot/スキャナ対策を有効化。
- レート制御（L7）を軽くかける（アプリ内レート制限と二重化）。

---

## 4. アプリ層のセキュリティ

### 4.1 クライアント認証（いずれか/併用可）
- **Google IAP JWT**（推奨 for ブラウザベースアプリ）：IAP が発行する JWT トークンを検証。Google Workspace 認証と統合可能。
- **短寿命JWT**：RS256、TTL 5〜15分、`kid`で鍵ローテ。サーバ側は**公開鍵のみ**保持。
- **HMAC + Timestamp**：`X-Timestamp` ±300秒、`X-Signature`（メソッド/パス/ボディハッシュを署名）。

> いずれの場合も**漏えい時の影響時間を最小化**。クライアントID/プロダクトIDを必ず埋め込む。

### 4.2 レート制限
- **Token Bucket** を Redis で実装。キー例：`rl:{product}:{user}`。
- 2段（QPS & 日次上限）で暴走/誤用をブロック。超過時は HTTP 429。

### 4.3 サーバ側パラメータ制限
- 許可モデルのホワイトリスト化、`max_tokens` 上限、`temperature` 範囲など。
- リクエストサイズ/回数の制限、ストリーミング時間上限。

### 4.4 Secrets 管理

#### 4.4.1 Secret Manager の使用（推奨）
本プロキシは **Secret Manager 統合機能** を実装しており、以下の方法でシークレットを管理できます：

**設定方法:**
```bash
# 1. Secret Manager にシークレットを作成
gcloud secrets create openai-api-keys \
  --data-file=product-keys.json \
  --replication-policy=automatic

# 2. Cloud Run サービスアカウントに権限を付与
gcloud secrets add-iam-policy-binding openai-api-keys \
  --member="serviceAccount:run-proxy-sa@<project>.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

# 3. Cloud Run デプロイ時に環境変数を設定
gcloud run deploy api-key-server \
  --set-env-vars "USE_SECRET_MANAGER=true" \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=<project-id>"
```

**動作:**
- `USE_SECRET_MANAGER=true` が設定されている場合、起動時に Secret Manager からシークレットを取得
- 環境変数が未設定の場合のみ Secret Manager から読み込む（環境変数が優先）
- デフォルトのシークレット名: `openai-api-keys`, `jwt-public-keys`, `hmac-secrets`
- シークレット名は環境変数でカスタマイズ可能（`API_KEY_SERVER_SECRET_PRODUCT_KEYS_NAME` など）

**メリット:**
- OpenAIキー等は **Secret Manager** に格納し、**参照権限のみ**を Cloud Run SA に付与
- **環境変数へプレーン展開しない**設計（ランタイムで取得・キャッシュ）
- バージョン管理とローテーションが容易
- 監査ログによるアクセス追跡が可能
- **ログ・例外**に機密を出さない（マスク設定必須）

**ベストプラクティス:**
- 本番環境では必ず Secret Manager を使用
- 開発環境では環境変数を使用（ローカルテスト用）
- シークレットのバージョンは `latest` ではなく特定バージョンを指定することを検討

### 4.5 マルチプロバイダー設定

本プロキシは **複数のAIプロバイダーを同時に使用できる設定** をサポートしています。

#### 4.5.1 設定形式

**新形式（マルチプロバイダー、推奨）:**
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

**従来形式（OpenAIのみ、後方互換）:**
```json
{
  "product-a": "sk-xxxxxxxxxxxxx",
  "product-b": "sk-yyyyyyyyyyy"
}
```

#### 4.5.2 プロバイダー選択ロジック

1. クライアントが OpenAI 互換形式でリクエストを送信（モデル名を含む）
2. サーバーはモデル名を確認し、設定された各プロバイダーの `models` リストと照合
3. マッチしたプロバイダーのアダプターを使用してリクエストを変換・転送
4. プロバイダーからのレスポンスを OpenAI 互換形式に変換して返却

#### 4.5.3 サポート対象プロバイダー

| プロバイダー | 実装ファイル | 変換処理 | モデル例 |
| --- | --- | --- | --- |
| OpenAI | `app/providers/openai.py` | なし（標準形式） | gpt-4o, gpt-4o-mini, gpt-3.5-turbo |
| Google Gemini | `app/providers/gemini.py` | リクエスト/レスポンス変換あり | gemini-1.5-pro, gemini-1.5-flash |
| Anthropic Claude | `app/providers/anthropic.py` | リクエスト/レスポンス変換あり | claude-3-5-sonnet-20241022, claude-3-opus-20240229 |

#### 4.5.4 課金・利用量分離

同一プロバイダーでも別々のAPIキーを設定することで、プロジェクトごとの課金を分離できます：

```json
{
  "research-team": {
    "providers": {
      "gemini": {
        "api_key": "AIzaSy_RESEARCH_PROJECT_KEY",
        "models": ["gemini-1.5-pro"]
      }
    }
  },
  "development-team": {
    "providers": {
      "gemini": {
        "api_key": "AIzaSy_DEVELOPMENT_PROJECT_KEY",
        "models": ["gemini-1.5-flash"]
      }
    }
  }
}
```

#### 4.5.5 新規プロバイダーの追加

新しいAIプロバイダーを追加する場合：

1. `app/providers/` 配下に新しいプロバイダークラスを作成
2. `call()` メソッドを実装（`api_key`, `payload`, `base_url`, `timeout` を受け取る）
3. OpenAI形式へのリクエスト/レスポンス変換ロジックを実装
4. `app/upstream.py` の `PROVIDERS` 辞書に登録
5. Secret Manager の設定に新プロバイダーのキーを追加

---

## 5. 監査・可観測性

### 5.1 ログ
- 重要フィールド：`timestamp, product, user, model, tokens_in/out, latency_ms, status`。
- 失敗理由を分類（認証失敗/レート超過/上流エラー）。
- 機密文字列（プロンプト等）が必要な場合は**要マスキング/要同意**。基本は**要約ログ**。

### 5.2 メトリクス/アラート
- **日次トークン使用量**の上限超過でアラート。
- **失敗率/レイテンシ/スパイク**の SLO 逸脱で通知。
- Redis 接続/エラー率/容量も監視。

---

## 6. デプロイ & 設定（例）

> 実行コマンドはプロジェクト名/リージョン/サービス名に合わせて読み替える。

### 6.1 サービスアカウント & 権限
- Cloud Run 実行 SA: `run-proxy-sa@<project>.iam.gserviceaccount.com`
  - 付与: `Secret Manager Secret Accessor`, `Logging Writer`, `Monitoring Metric Writer`, `Redis Client` など最小権限。

### 6.2 Secret Manager
**シークレットの作成:**

**マルチプロバイダー設定（推奨）:**
```bash
# プロダクトキー（マルチプロバイダー形式）
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

**従来形式（OpenAIのみ、後方互換）:**
```bash
# プロダクトキー（従来形式）
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

# JWT公開鍵（JSON形式）
cat > jwt-keys.json <<EOF
{
  "kid-1": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----"
}
EOF

gcloud secrets create jwt-public-keys \
  --data-file=jwt-keys.json \
  --replication-policy=automatic

# HMAC共有鍵（JSON形式）
gcloud secrets create hmac-secrets \
  --data-file=hmac-secrets.json \
  --replication-policy=automatic

# ローカルファイルを削除
rm product-keys.json jwt-keys.json hmac-secrets.json
```

**権限の付与:**
```bash
SERVICE_ACCOUNT="run-proxy-sa@<project>.iam.gserviceaccount.com"

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

**キーローテーション（カナリア方式）:**
```bash
# 1. 新しいバージョンを追加
gcloud secrets versions add openai-api-keys --data-file=new-keys.json

# 2. アプリケーションを再起動して新バージョンを取得
gcloud run services update api-key-server --region <region>

# 3. 動作確認後、古いバージョンを無効化
gcloud secrets versions disable 1 --secret=openai-api-keys
```

### 6.3 Memorystore (Redis)
- 低プランで開始。レプリカ/耐障害は必要に応じて。
- VPC Connector 経由で Cloud Run から私設アクセス。

### 6.4 Cloud Run デプロイ例

#### 6.4.1 Secret Manager を使用する場合（推奨）
```bash
PROJECT_ID="<your-project-id>"

gcloud run deploy api-key-server \
  --image gcr.io/${PROJECT_ID}/api-key-server:latest \
  --region asia-northeast1 \
  --platform managed \
  --service-account run-proxy-sa@${PROJECT_ID}.iam.gserviceaccount.com \
  --memory 512Mi --cpu 1 \
  --ingress internal-and-cloud-load-balancing \
  --allow-unauthenticated=false \
  --set-env-vars "USE_SECRET_MANAGER=true" \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT_ID}" \
  --set-env-vars "API_KEY_SERVER_REDIS_URL=redis://:password@<redis-host>:6379/0" \
  --vpc-connector <connector-name>
```

#### 6.4.2 環境変数を使用する場合（非推奨）
```bash
gcloud run deploy api-key-server \
  --image gcr.io/<project>/api-key-server:latest \
  --region <region> \
  --platform managed \
  --service-account run-proxy-sa@<project>.iam.gserviceaccount.com \
  --memory 512Mi --cpu 1 \
  --ingress internal-and-cloud-load-balancing \
  --allow-unauthenticated=false \
  --set-env-vars "API_KEY_SERVER_PRODUCT_KEYS={\"product-a\":\"sk-xxx\"}" \
  --set-env-vars REDIS_HOST=<host>,REDIS_PORT=<port> \
  --vpc-connector <connector-name>
```

### 6.5 HTTPS LB + IAP + Cloud Armor 構成
1. サーバレスNEGで Cloud Run をバックエンド登録。
2. HTTPS LB を作成し、**Cloud Armor** ポリシーを適用（WAF & IP許可）。
3. **IAP を有効化**し、許可ユーザー/グループを設定。
4. 必要に応じて **IDトークン**で直接保護（Authenticated invocations）。

### 6.6 CORS/Headers
- `Access-Control-Allow-Origin` は**社内Webの正確なFQDNのみ**。
- `Authorization`/`X-*` ヘッダの露出は最小限。`OPTIONS` 応答も厳密。

---

## 7. バックエンド実装方針（FastAPI 概要）

- 主要エンドポイント：`POST /chat/{product}`（`stream` 有/無）。
- **認証ミドルウェア**：JWT or HMAC を最初に検証、`product/user` をコンテキスト化。
- **レート制限**：`rl:{product}:{user}` をチェック。超過で 429。
- **外部API呼出**：モデル/上限を強制、失敗時は分類して 4xx/5xx。
- **監査ログ**：レスポンス確定時に書き込む（失敗も含む）。

---

## 8. セキュリティ運用チェックリスト

- [ ] Cloud Run 認証必須 & IAP/IDトークン保護
- [ ] Cloud Armor（WAF + IP許可 + 基本Rate）
- [ ] 短寿命 JWT or HMAC+Timestamp（TTL 5〜15分）
- [ ] ユーザー/プロダクト別レート制限（QPS + 日次上限）
- [ ] Secret Manager（鍵はコード/ENVに直置きしない）
- [ ] ログのマスキング & 機密の最小記録
- [ ] 鍵ローテ（新旧併用→切替→旧失効）
- [ ] 監査メトリクス/アラート（使用量/失敗率/スパイク）
- [ ] CORS厳格化 & ヘッダ最小化
- [ ] 依存ライブラリとベースイメージの定期更新

---

## 9. 障害対応・インシデント手順（サマリ）

1. **濫用検知**（スパイク/日次超過アラート） → Cloud Armor で即時ブロック（IP/パス）。  
2. **アプリ側遮断**：対象 `product/user` のレート上限をゼロ化 or ブラックリスト。  
3. **鍵入替**：該当 Project の API キーを Secret Manager で更新、旧キー無効化。  
4. **原因調査**：監査ログ（`product/user/model/量`）で追跡、再発防止策を作成。  
5. **復旧**：段階的に上限を戻し、影響範囲を確認。

---

## 10. コスト最適化メモ

- Cloud Run は**ゼロスケール**でアイドルコストほぼゼロ。メモリ/CPU を最小から開始。  
- Redis は小容量で開始し、レート制限ロジックを軽量化（キー期限を短めに）。  
- ログは**要点のみ**を構造化（全文/ペイロードは保護と費用の両面で抑制）。

---

## 11. 将来拡張

- **プロダクト追加**：Secret Manager の設定に新しいプロダクトとプロバイダー設定を追加するだけ。コード変更不要。
- **新規プロバイダー追加**：`app/providers/` に新プロバイダークラスを追加し、`upstream.py` の `PROVIDERS` 辞書に登録。
- **社外公開への段階的移行**：IAP 外し + 強めのアプリ認証/Cloud Armor/プライシング制御を追設。
- **多地域**：利用者分布に応じてリージョンを追加、LB で振り分け。
- **ストリーミング対応**：各プロバイダーアダプターでストリーミングレスポンスの変換を実装。

---

## 付録A: HMAC 署名仕様（例）

- ヘッダ: `X-Timestamp`（UNIX秒）, `X-Signature`（hex）  
- 署名対象:  
  ```text
  <timestamp>\n<HTTP_METHOD>\n<PATH>\n<body_sha256_hex>
  ```
- アルゴリズム: HMAC-SHA256（共有鍵）  
- 許容時差: ±300秒（リプレイ対策）

---

## 付録B: JWT 仕様（例）

- ヘッダ: `kid` に鍵ID、`alg`=`RS256`  
- クレーム: `iss`（発行者）, `sub`（user_id）, `aud`（service_id）, `exp`（5〜15分）, `product`  
- サーバは**公開鍵**で署名検証。`kid`に基づき鍵をローテーション可能。

---


---

## 12. 運用モードの拡張（A → B へ強化）

本設計のデフォルトは **A: Workspaceユーザーのみ（IAP/認証必須）**。
必要に応じて **B: Workspaceユーザーのみ + 社内IP/VPN 制限** へ段階的に強化する。**API 形状やクライアント実装は変更不要**。

### 12.1 強化の狙い
- アカウント流出やソーシャルエンジニアリングに対して、**ネットワーク面の第二防護線**を追加。
- 「社外からは原則使わせない」という利用方針を技術的に担保。

### 12.2 変更点（A → B）
- **IAP/認証必須は維持**（Workspace 限定の ID ベース制御）。
- **Cloud Armor のポリシーに IP 許可リスト**を追加し、**社内拠点／VPN の出口IP のみ許可**。
- 必要に応じて **国/ASN のブロック**などの基礎的WAFルールを有効化。

### 12.3 影響範囲
- **クライアント**：変更不要（社内ネットワーク/VPN 経由で到達すれば従来通り）。
- **運用**：拠点追加やVPN更改時に**許可IPの更新**が必要。更新はメンテ手順に組み込む。

### 12.4 ロールバック
- 緊急時（VPN 障害等）は **IP 許可リストを一時的に緩和**（IAP 保護は継続）。
- 影響が解消したら **元の許可リスト**へ復帰。

### 12.5 A/B どちらでも共通のアプリ内防御
- **短寿命JWT/HMAC** によるクライアント認証（TTL 5–15分）。
- **ユーザー/プロダクト別レート制限**（QPS + 日次上限）。
- **モデル/パラメータのホワイトリスト**、**監査ログ/アラート**。

---

## 13. 実際の GCP 設定手順書について

本ドキュメントは**設計書（方針・構成・責務分担）**であり、実際の `gcloud` / コンソール操作手順は
別の **「セットアップ手順書（Runbook）」** として作成することを推奨する。理由：

- **変更しやすさ**：プロジェクト/リージョン/命名規則/IPリスト等の“環境依存値”を分離できる。
- **レビュー性**：設計と手順を分けると、セキュリティレビューと実務手順レビューを個別に回せる。
- **運用性**：手順書は**バージョン化**（Git）し、更新履歴・変更差分を明確化できる。

### 13.1 手順書のアウトライン例
1. 事前準備（プロジェクト、役割、命名規則）  
2. サービスアカウント作成と権限付与  
3. Secret Manager の作成とキー登録  
4. Cloud Run デプロイ（認証必須 / VPC Connector 等）  
5. HTTPS LB とサーバレスNEGの設定  
6. IAP 有効化と許可ユーザー/グループ設定（＝A 完了）  
7. Cloud Armor ポリシー作成・適用と IP 許可リスト登録（＝B への強化）  
8. 監査ログ/メトリクス/アラート設定  
9. 動作確認（IAP/非許可IP/許可IP/レート制限/鍵ローテ）  
10. 運用（IPリスト更新/鍵ローテ/障害時ロールバック）


以上。
