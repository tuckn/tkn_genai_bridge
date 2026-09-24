# 共有設定の仕様

## 設定の読み込み

補助CLIは次の順で読み、後の値を優先します。

1. 組み込み既定値
2. `~/.tkn/genai_bridge/config.yaml`
3. 実行時の `./.tkn/config.yaml`
4. `--config` で明示したファイル
5. `--profile`、`--model` などの個別オプション

`--no-project-config` で3番を省略できます。
各設定ファイルは共通パッケージのスキーマで書いてください。
アプリ固有の設定ファイルを `--config` に指定すると、未知のキーを検出して停止します。

ライブラリの `load_config()` と `load_profile()` は、3番を既定で省略します。
利用側アプリの設定と衝突させないためです。
`load_config(include_project=True)` の場合は5段階の解決を使えます。
`load_profile(..., overrides={...})` の上書きは1つのプロファイルのフィールドが対象です。
`load_config(overrides={...})` は設定全体の構造で指定します。

入れ子のマッピングは再帰的に統合し、値と `null` は置き換えます。
プロバイダーを変更するときは、前のプロバイダー専用設定を `null` にするか、新しいプロファイルを作成してください。
`profiles` の名前は識別子であり、`azure-quality` のような名前を使えます。
設定プロパティ自体は `snake_case` です。

`config show` は最終値、読み込んだファイル・版、各項目を採用した設定元を表示します。
APIキーの環境変数名は表示しますが、環境変数の値を取得・表示しません。
設定内容を表示する操作なので、接続先や実行ファイルのパスを第三者に共有する際は利用者が確認してください。

## スキーマの版と検証

新規設定の先頭の項目は `schema_version: "1.1.0"` です。
`1.0.x` と `1.1.x` を受け付け、解決後の設定は `1.1.0` として返します。既存ファイルは書き換えません。
欠落、不正な形式、`1.2.0` 以降の Minor、他の Major は拒否します。
旧 Major からの自動移行はありません。

各ファイルの版、未知のキー、型を統合前に検証し、統合後には必要項目・URL・接続先との整合を検証します。
文字列の数値や `true` を秒数に変換するような暗黙の型変換は行いません。
重複した YAML キーもエラーになります。
設定の読み込みでファイルを書き換えることはありません。

## 共通項目

`schema_version`、`default_profile`、`profiles` がトップレベルの項目です。
`profiles` の各要素には次のフィールドを指定できます。

| キー | 既定値 | 意味 |
| --- | --- | --- |
| `pricing` | `{}` | モデル／Azureデプロイ名から参考単価へのマッピング。[コスト概算](costs.md)を参照 |
| `provider` | `codex` | `codex` / `claude-code` / `github-copilot` / `antigravity` / `ollama` / `azure-openai` |
| `model` | `null` | CLIでは製品の既定モデル。APIでは必須。Azureではデプロイ名 |
| `reasoning_effort` | `null` | 省略時は送信しない。接続先とモデルが対応する値を指定 |
| `timeout_seconds` | `300.0` | 正の秒数。上限86400。CLIプロセスの待機／HTTP各I/Oの待機 |
| `max_output_tokens` | `null` | APIのみ。出力トークン上限。モデルの思考分を含む意味は接続先による |
| `local_only` | `false` | `true` は Ollama のみ。ローカルモデル確認を追加 |
| `cli` | `null` | CLI専用の設定 |
| `ollama` | `null` | Ollama専用の設定。省略時は下表の既定値 |
| `azure` | `null` | Azure専用の設定。Azure使用時は必須 |

`model: null` は CLI に `--model` を渡しません。
実際のモデル名が応答から取得できない場合、結果の `response_model` も `null` になります。
設定した名前を実際のモデル名として代用しません。

推論設定の許容値は次のとおりです。
ここにある値でも、選択したモデルが対応しない場合は接続先で失敗します。

| 接続先 | `reasoning_effort` |
| --- | --- |
| Codex | `none`、`minimal`、`low`、`medium`、`high`、`xhigh`、`max`、`ultra` |
| Claude Code / GitHub Copilot | `low`、`medium`、`high`、`xhigh`、`max` |
| Google Antigravity | `low`、`medium`、`high` |
| Azure OpenAI | `none`、`minimal`、`low`、`medium`、`high`、`xhigh` |
| Ollama | この項目は拒否。`ollama.think` を使用 |

タイムアウトは処理全体の予算ではありません。
Ollama のモデル確認と生成は別の HTTP 要求です。
Azure の認証待機は認証ライブラリが管理するため、`timeout_seconds` とは別です。

## 接続先固有の項目

### CLI

`cli.executable` は実行ファイル名またはパスです。
省略時は `codex`、`claude`、`copilot`、`agy` を PATH から探し、Windows の既知のインストール先を補完します。
相対パスは呼び出し時の作業ディレクトリ、`~` はホームを基準にします。

`.cmd`、`.bat`、`.ps1` のシェル中継は実行しません。
ネイティブ実行ファイルを指定してください。
Codex の WindowsApps ランチャーも拒否します。

### Ollama

| キー | 既定値 | 意味 |
| --- | --- | --- |
| `base_url` | `http://127.0.0.1:11434` | パス・認証情報・クエリのないループバックURL |
| `think` | `null` | `true` / `false` / `low` / `medium` / `high` / `max`。省略時は送信しない |
| `context_tokens` | `null` | 正の整数。`options.num_ctx` に送る |
| `temperature` | `0.0` | 0〜2。モデルの生成温度 |

ループバックは `localhost`、`127.0.0.1`、`[::1]` を受け付けます。
環境変数の HTTP プロキシとリダイレクトは使いません。
`local_only: true` ではクラウドを示すモデル名を拒否し、生成前に `/api/show` のローカルモデル情報を確認します。
情報不足やリモートモデルなら、プロンプトを送らずに停止します。
サーバー側のクラウド無効化も必要です。詳細は [接続仕様](providers.md) を参照してください。

### Azure OpenAI

| キー | 既定値 | 意味 |
| --- | --- | --- |
| `endpoint` | `null` | 必須。HTTPS で `/openai/v1` まで指定 |
| `auth` | `api_key` | `api_key` / `default_credential` / `interactive_browser` / `token_provider` |
| `api_key_env` | `AZURE_OPENAI_API_KEY` | キーを保持する環境変数名。キー本体は記載しない |
| `tenant_id` | `null` | 対話ブラウザー認証のテナント |
| `token_scope` | `https://cognitiveservices.azure.com/.default` | Entra IDのトークンスコープ |

`default_credential` は Azure Identity の既定の資格情報探索を使い、対話ブラウザー認証は無効です。
その認証経路に必要な環境変数・Azure CLIログインなどは事前に準備してください。
`interactive_browser` を明示した場合だけ、生成時にブラウザー認証を利用します。
パッケージ独自の永続認証キャッシュは作成しません。

`token_provider` はライブラリで `Runtime(profile, token_provider=callback)` として渡すコールバック用です。
補助CLIはコールバックを受け付けないため、この認証方式では生成できません。
利用側が認証やトークンの再利用を管理する場合に使えます。

## 6種類のプロファイルを定義する例

次の内容で共有設定を置き換えられます。
`your-local-model`、`your-deployment-name`、`your-resource` を実際の値へ変更してください。
設定だけではサーバーの起動、モデル取得、認証の準備は行われません。

```yaml
schema_version: "1.1.0"
default_profile: codex-default
profiles:
  codex-default:
    provider: codex
    model: null
  claude-default:
    provider: claude-code
    model: null
  copilot-default:
    provider: github-copilot
    model: null
  antigravity-default:
    provider: antigravity
    model: null
    timeout_seconds: 300.0
    cli:
      executable: agy
  local:
    provider: ollama
    model: your-local-model
    local_only: true
    max_output_tokens: 2048
    ollama:
      base_url: http://127.0.0.1:11434
      think: false
  azure-quality:
    provider: azure-openai
    model: your-deployment-name
    max_output_tokens: 4096
    azure:
      endpoint: https://your-resource.openai.azure.com/openai/v1
      auth: api_key
      api_key_env: AZURE_OPENAI_API_KEY
```

APIキーは利用者が環境変数に用意してください。
パッケージはキーの作成や変更を行いません。

## 参考単価

`pricing` は全プロバイダーで使用できます。単価未設定でもtoken概算・生成は可能です。
単価を設定すると `plan().cost_estimate` と実行記録の `cost_estimate` に参考額を返します。
`max_cost_jpy`、許可・停止の設定、実行回数上限はBridgeの設定項目ではありません。
それらは利用側CLIに保持してください。

価格はモデル名の完全一致で選びます。`--model` で変更したモデルに単価がなければ金額は不明です。
CLIのモデルが `null` の場合、事前の単価は不明で、実行後に応答モデル名が取得できた場合だけ完全一致で選択します。
Azureは常に要求したデプロイ名で選択し、返されたモデル名に別の単価を推測適用しません。

マッピングは再帰的に統合されるため、単価の一部だけを追加ファイルや `overrides` で更新できます。
各単価の設定元は `config show` の `field_sources` で確認できます。
`pricing: {}` は上位設定にある単価の削除にはなりません。価格を切り離す場合は別プロファイルを使用してください。
