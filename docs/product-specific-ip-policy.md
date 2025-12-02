
# プロダクト別に IP 制限を切り替える設計／手順（Addendum)
最終更新: 2025-12-02 (Asia/Tokyo)

対象プロジェクト: `interview-api-472500`（変更可）

本書は、**特定のプロダクトのみ IP を許可リストで制限し、他プロダクトは IAP 認証のみで社外からも利用可**にする構成と手順を示す。  
ベースは既存の **A（IAP/Workspace限定）** 構成。ここでは **LB のバックエンドサービスを分けて** Cloud Armor ポリシーを差し替える方式（推奨）を中心に記述し、代替案も示す。

---

## 0. 事前条件
- Cloud Run（`chat-proxy` など）を A 構成でデプロイ済み（IAP 有効）
- サーバレス NEG + HTTPS LB（IAP有効）が存在
- ドメイン/FQDN（例: `api.example.co.jp`）は LB に割り当て済み

> ここでは、**制限対象のプロダクト**を `prod_restricted`、それ以外を `prod_open` と呼ぶ。  
> 実際のプロダクト名は `{product}` パスに合わせて置換する。

---

## 1. 方針（概要）
- **URL マップでパスごとにバックエンドサービスを振り分け**る：
  - `/v1/chat/prod_restricted` → **backend-restricted**（Cloud Armor: IP 許可リスト付き）
  - `/v1/chat/*`（その他） → **backend-open**（Cloud Armor: 制限なし／WAFのみ）
- **IAP は両者で共通に有効**（Workspace 限定）。
- クライアント実装は変更不要（同じ LB/FQDN 宛、パスのみで制御）。

---

## 2. 作成物（命名例）
- バックエンドサービス:  
  - `be-chat-open`（制限なし）  
  - `be-chat-restricted`（IP 許可つき）
- Cloud Armor ポリシー:  
  - `armor-chat-open`（WAF標準のみ or 無し）  
  - `armor-chat-restricted`（**IP許可ルール**を実装）

> サーバレス NEG は **共通**で OK（Cloud Run サービスは同一）。

---

## 3. Cloud Armor（restricted 用）作成
**許可する出口IP (社内/VPN)** を CIDR で列挙する。例: `203.0.113.10/32`, `198.51.100.0/24`。

```bash
gcloud compute security-policies create armor-chat-restricted   --description="Chat proxy: IP allow for restricted product"

# 1000番（小さいほど優先度が高い）で Allow ルールを作成
gcloud compute security-policies rules create 1000   --security-policy armor-chat-restricted   --action=allow   --description="Office/VPN egress IPs"   --expression="inIpRange(origin.ip, ['203.0.113.10/32','198.51.100.0/24'])"

# 既定の最後のルール（No.2147483647）は deny-403（デフォルト）
```

> 追加の WAF ルール（国/ASN ブロック等）を載せる場合は、1000 の後に 1100, 1200... を追加。

---

## 4. バックエンドサービスを 2 つ用意（同一NEGを参照）
> 既に `be-chat-open` がある場合は「更新」、無ければ「作成」。`<NEG_NAME>` は既存のサーバレスNEG名に置換。

```bash
# 例: open 側（Armor なし or 別ポリシー）
gcloud compute backend-services create be-chat-open   --load-balancing-scheme=EXTERNAL_MANAGED   --global

gcloud compute backend-services add-backend be-chat-open   --global   --network-endpoint-group=<NEG_NAME>   --network-endpoint-group-zone=<NEG_ZONE or --global-neg>
# Armor を付ける場合:
# gcloud compute backend-services update be-chat-open --global --security-policy armor-chat-open

# restricted 側（Armor: IP 許可）
gcloud compute backend-services create be-chat-restricted   --load-balancing-scheme=EXTERNAL_MANAGED   --global

gcloud compute backend-services add-backend be-chat-restricted   --global   --network-endpoint-group=<NEG_NAME>   --network-endpoint-group-zone=<NEG_ZONE or --global-neg>

gcloud compute backend-services update be-chat-restricted   --global --security-policy armor-chat-restricted
```

> 既存の LB が単一バックエンドの場合、**URL マップ**を「複数バックエンドに対応する形」に再作成/更新する。コンソール操作の方が視覚的でミスが少ない。

---

## 5. URL マップのルーティングを設定
- 既存 URL マップに **パスベース ルール**を追加：
  - `^/v1/chat/prod_restricted($|/.*)` → `be-chat-restricted`
  - それ以外 → `be-chat-open`

> 代表的な構成:  
> - フロントエンド（HTTPS/証明書）  
> - ターゲット HTTPS プロキシ（URL マップを参照）  
> - URL マップ（上記パスルール）  
> - バックエンドサービス（`be-chat-open` / `be-chat-restricted`）  
> - バックエンド（共通のサーバレス NEG → Cloud Run）

**gcloud 例（簡略）**: URL マップの JSON 定義を編集して反映する方法。

```bash
# 1) 既存URLマップを取得
gcloud compute url-maps export <URL_MAP_NAME> --global --destination=urlmap.yaml

# 2) urlmap.yaml を編集: pathMatcher.defaultService を be-chat-open に、
#    pathRules に以下を追加（例）
#  - paths:
#    - /v1/chat/prod_restricted
#    - /v1/chat/prod_restricted/*
#    service: https://www.googleapis.com/compute/v1/projects/<PROJECT_ID>/global/backendServices/be-chat-restricted

# 3) 反映
gcloud compute url-maps import <URL_MAP_NAME> --global --source=urlmap.yaml
```

> コンソール操作では「ロードバランサ > ルーティング ルール > パスマッチャー」で、上記2本のルールを作成。

---

## 6. IAP（共通）
- IAP は **バックエンドサービス単位**で有効化/権限付与される。  
- 既に IAP 有効なバックエンドがある場合、**新規の `be-chat-restricted` に対しても IAP を有効化**し、**同じ許可グループ**を付与する。

```bash
# IAP 有効化（バックエンドサービス名は置換）
gcloud iap web enable --resource-type=backend-services --name=be-chat-restricted

# 許可グループの付与
gcloud iap web add-iam-policy-binding   --resource-type=backend-services   --name=be-chat-restricted   --member="group:allowed-users@i-seifu.jp"   --role="roles/iap.httpsResourceAccessor"
```

---

## 7. 動作確認
1. **社外回線**から `/v1/chat/prod_restricted` にアクセス → IAP 認証後 **403 (Armor)** を確認。  
2. **社内/VPN**からは同パスが **200** で利用可能。  
3. `/v1/chat/prod_open`（またはその他のプロダクト）は、社外回線でも IAP 認証さえ通れば **200**。  
4. アプリ層（FastAPI）の **JWT/HMAC** と **レート制限**も併せて正常動作を確認。

---

## 8. 運用
- **IP 追加/更新**: `armor-chat-restricted` のルール 1000 の `expression` を更新。  
- **一時緩和**（障害対応）: restricted の Backend Service から一時的に Armor を外す（IAP は維持）。  
- **プロダクト追加**: 新しい制限が必要なら、同様に **パスルール**と **Armor** を追加。

---

## 9. 代替案（参考）
### 9.1 サブドメイン分離
- `restricted.api.example.co.jp` → `be-chat-restricted`  
- `api.example.co.jp` → `be-chat-open`  
DNS/証明書が分かれるため運用が分かりやすい一方、設定点が増える。

### 9.2 アプリ層でのIPチェック（補助線）
- LB を通過した後、FastAPI が `X-Forwarded-For` を検証して **{product} ごとの IP 許可**を行う。  
- **第一次防御は LB/Armor** に任せ、この方法は補助として推奨。

---

## 10. 変更の影響（まとめ）
- クライアント：**パスで切り替わるだけ**（FQDN は共通のまま運用可）。  
- 監査：`{product}` と **バックエンド名/Armorヒット**をログに出すと、判別と調査が容易。  
- セキュリティ：IAP（IDベース） + Armor（IPベース）の **二重ロック**。

---

以上。
