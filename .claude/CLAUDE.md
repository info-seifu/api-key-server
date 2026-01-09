# プロジェクト AI ガイド（API Key Proxy Server）

> 共通ルール（`~/.claude/CLAUDE.md`）に加えて、このプロジェクト固有のルールを定義します。
> 運用手順は `docs/OPERATIONS.md` を参照してください。

---

## 1. プロジェクト概要

| 項目 | 内容 |
|-----|------|
| **名前** | API Key Proxy Server |
| **概要** | FastAPIベースのAI APIプロキシ。上流APIキーをサーバー側で管理 |
| **対応API** | OpenAI、Google Gemini、Anthropic Claude |
| **認証** | JWT (RS256)、HMAC + タイムスタンプ、Google IAP |
| **デプロイ** | Google Cloud Run |

---

## 2. 技術スタック

| カテゴリ | 技術 |
|---------|------|
| **言語** | Python 3.11 |
| **フレームワーク** | FastAPI 0.111.0、Pydantic 2.7.4 |
| **HTTP** | HTTPX 0.27.0（非同期）、Uvicorn 0.30.1 |
| **認証** | Python-Jose 3.3.0、Google Auth 2.43.0 |
| **インフラ** | Docker、Cloud Run、Secret Manager、Redis（オプション） |

---

## 3. ディレクトリ構成

```
app/
├── main.py          # FastAPIアプリ
├── config.py        # 設定管理
├── auth.py          # 認証処理（JWT/HMAC/IAP）
├── rate_limit.py    # レート制限
├── secrets.py       # Secret Manager統合
├── upstream.py      # プロバイダ委譲
└── providers/
    ├── openai.py    # OpenAIプロバイダ
    ├── gemini.py    # Geminiプロバイダ
    └── anthropic.py # Claudeプロバイダ
```

---

## 4. 認証メカニズム

### 4.1 対応方式

| 方式 | ヘッダー | 実装 |
|-----|---------|------|
| JWT (RS256) | `Authorization: Bearer <token>` | `app/auth.py:_verify_jwt()` |
| HMAC | `X-Timestamp`, `X-Signature`, `X-Client-ID` | `app/auth.py:_verify_hmac()` |
| IAP | `X-Goog-IAP-JWT-Assertion` | `app/auth.py:_verify_iap()` |

### 4.2 HMAC署名形式

```python
message = f"{timestamp}\n{method}\n{path}\n{body_hash}"
signature = hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()
```

- タイムスタンプ許容範囲: 300秒（環境変数で調整可）

---

## 5. エラーハンドリング

### 5.1 HTTPステータスコード

| ステータス | 使用場面 |
|-----------|----------|
| 200 | 成功 |
| 400 | max_tokens超過、モデル未対応、ストリーミング要求 |
| 401 | 認証失敗（JWT/HMAC/IAP） |
| 404 | プロダクト未登録 |
| 429 | レート制限超過 |
| 502 | 上流APIエラー（**詳細は隠蔽**） |
| 500 | 内部エラー |

### 5.2 セキュリティルール

- **上流エラー詳細は隠蔽**: すべて `502 Bad Gateway` として返す
- **APIキーはログに出力しない**: `logger.error(f"API key: {api_key}")` は禁止
- エラー詳細はログに記録し、クライアントには一般的なメッセージのみ

---

## 6. マルチプロバイダ対応

### 6.1 プロダクト設定形式

```json
{
  "product-a": {
    "providers": {
      "openai": { "api_key": "sk-...", "models": ["gpt-4o"] },
      "gemini": { "api_key": "AIza...", "models": ["gemini-1.5-pro"] }
    }
  }
}
```

### 6.2 プロバイダ選択

1. リクエストの`model`パラメータを取得
2. 各プロバイダの`models`リストを確認
3. マッチするプロバイダを使用（なければ400エラー）

### 6.3 プロバイダインターフェース

すべてのプロバイダは以下を実装（`app/providers/openai.py`を参照）:

```python
async def call(api_key: str, payload: dict, timeout: int) -> dict
async def call_image(api_key: str, payload: dict, timeout: int) -> dict
async def call_audio(api_key: str, payload: dict, timeout: int) -> dict
```

**重要**: レスポンスはすべてOpenAI互換形式に統一

---

## 7. コーディング規約

### 7.1 基本ルール

- **非同期処理**: すべてのAPI呼び出しは `async/await`
- **型ヒント**: すべての関数に付与
- **Pydantic**: リクエスト/レスポンスの検証に使用
- **依存性注入**: FastAPIの`Depends`を活用

### 7.2 エラーハンドリングパターン

```python
try:
    response = await provider.call(api_key, payload, timeout)
    return response
except httpx.HTTPStatusError as e:
    logger.error(f"Upstream API error: {e.response.status_code}")
    raise HTTPException(status_code=502, detail="Upstream service error")
```

---

## 8. 品質チェック

### 8.1 チェックフロー

```
1. 実装完了
2. python -m py_compile app/*.py app/providers/*.py
3. レビュー実施（共通ルール セクション4参照）
4. 指摘事項を修正
5. 再レビュー（指摘ゼロまで）
```

### 8.2 重点レビュー観点

| フェーズ | 対象 | チェック項目 |
|---------|------|-------------|
| Critical | auth.py, secrets.py | APIキーのログ出力、上流エラー隠蔽、認証バイパス |
| High | config.py, upstream.py | プロバイダ選択、レート制限、エラーハンドリング |
| Medium | providers/*.py | async/await、タイムアウト、ログレベル |

> 詳細なレビュー観点と自動修正ルールは共通ルール（`~/.claude/CLAUDE.md` セクション4）を参照

---

## 9. レート制限

| 設定 | デフォルト値 |
|------|-------------|
| バケット容量 | 10 リクエスト |
| 補充レート | 5 tokens/sec |
| 日次クォータ | 200,000 リクエスト |

- **インメモリモード**: デフォルト（単一インスタンス向け）
- **Redisモード**: `API_KEY_SERVER_REDIS_URL` を設定（分散環境向け）

---

## 10. Secret Manager

### シークレット一覧

| シークレット名 | 内容 |
|--------------|------|
| `openai-api-keys` | プロダクト設定（JSON） |
| `jwt-public-keys` | JWT公開鍵（JSON） |
| `hmac-secrets` | HMACシークレット（JSON） |

### ローカル開発

```bash
USE_SECRET_MANAGER=false  # ローカルファイルから読み込み
```

- `config/openai_api_keys.json`
- `config/jwt_public_keys.json`
- `config/hmac_secrets.json`

---

## 11. 環境変数

| 変数名 | デフォルト | 説明 |
|--------|-----------|------|
| `USE_SECRET_MANAGER` | `false` | Secret Manager使用フラグ |
| `API_KEY_SERVER_GCP_PROJECT_ID` | - | GCPプロジェクトID |
| `API_KEY_SERVER_MAX_TOKENS` | `2048` | max_tokens上限 |
| `API_KEY_SERVER_REQUEST_TIMEOUT_SECONDS` | `90` | HTTPタイムアウト |
| `API_KEY_SERVER_REDIS_URL` | - | Redis URL（オプション） |

---

## 12. 新プロバイダ追加

### チェックリスト

- [ ] `app/providers/<name>.py` を作成（`openai.py`をテンプレートに）
- [ ] `call()`, `call_image()`, `call_audio()` を実装
- [ ] `_convert_request()`, `_convert_response()` でフォーマット変換
- [ ] `app/upstream.py` の `PROVIDERS` 辞書に登録
- [ ] レスポンスはOpenAI互換形式に統一

---

## 13. 既知の制限

| 制限 | 詳細 |
|------|------|
| ストリーミング非対応 | `stream: true` は400エラー |
| テスト未実装 | `tests/` は空 |

---

## 14. 運用手順

> ローカル開発、CI/CD、デプロイ、トラブルシューティングは `docs/OPERATIONS.md` を参照

---

## 15. 更新ポリシー

- 機能追加・仕様変更時に更新
- 共通ルールと整合性を維持

### 更新履歴

- 2026-01-09: スリム化実施（1,347行 → 約250行）
  - 運用手順書を `docs/OPERATIONS.md` に分離
  - コード例を簡略化、実装ファイル参照に変更
  - 共通ルールとの重複を削除
