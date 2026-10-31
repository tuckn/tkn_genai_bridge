# Bridge 設定の仕様

## 設定の読み込み

補助CLIは既定で次の順で読み、後の値を優先します。

1. 組み込み既定値
2. `~/.tkn/genai_bridge/config.yaml`
3. 実行時の `./.tkn/config.yaml`
4. `--config` で明示したファイル
5. `--profile`、`--model` などの個別オプション

`--no-user-config` で2番、`--no-project-config` で3番をそれぞれ独立に省略できます。
各設定ファイルは共通パッケージのスキーマで書いてください。
アプリ固有の設定ファイルを `--config` に指定すると、未知のキーを検出して停止します。

ライブラリの `load_config()` と `load_profile()` は、3番を既定で省略します。
利用側アプリの設定と衝突させないためです。
`load_config(include_project=True)` の場合は5段階の解決を使えます。
`load_profile(..., overrides={...})` の上書きは1つのプロファイルのフィールドが対象です。
`load_config(overrides={...})` は設定全体の構造で指定します。

### 共有設定とアプリ専用設定の選択

`load_config()` と `load_profile()` は `include_user: bool = True` を受け付けます。
`False` にするとユーザー共通設定を読み込み対象から除外し、読み取り・YAML解析・スキーマ検証を行いません。
`load_config()` の `include_project: bool = False` は独立した指定です。
`load_profile()` は常に CWD 設定を読みません。

| 指定 | 契約 |
| --- | --- |
| `config_file=Path(...)` / `--config PATH` | 最後に読む明示ファイル。指定だけでは共有設定・CWD設定の扱いは変わらない |
| `include_user=False` / `--no-user-config` | ユーザー共通設定を省略。組み込み既定値は維持 |
| `include_project=False` / `--no-project-config` | CWD 設定を省略。Python API では既定、CLI では明示が必要 |
| `load_config(user_file=Path(...))` | ユーザー設定層のパスを置換。追加の明示ファイルではなく、従来どおり未存在なら省略 |
| `user_file=...` と `include_user=False` | `ConfigError(code="invalid_config")`。読み取り前に競合を拒否し、`user_file` を黙って無視しない |
| `user_file=...` と `config_file=...` | `include_user=True` なら両方を読み、後者を優先。従来どおり |

`user_file` は `load_config()` だけの引数です。専用設定には必須ファイルとして扱う `config_file` を使ってください。
`config_file` の指定は `include_user=False` の必須条件ではありません。
外部ファイルを指定せず両設定層を無効にした場合は、組み込み既定値と `overrides` だけを使用します。
相対パスは `load_config(cwd=...)`、省略時は実行時 CWD が基準です。`~` は展開します。
利用側アプリが専用設定の絶対パスを管理すれば、起動ディレクトリに依存しません。

共有設定を使う既存の呼び出し方は変わりません。

```python
from pathlib import Path
from tkn_genai_bridge import load_config, load_profile

shared = load_config()
profile = load_profile("codex-default")
# 明示ファイルだけの指定は、従来どおり共有設定への追加マージ
merged = load_config(config_file=Path("C:/path/to/extra.yaml"))
```

アプリ専用ファイルだけを読む場合は、次のように指定します。

```python
app_config = Path("C:/path/to/your_cli/bridge.yaml")
resolved = load_config(
    config_file=app_config,
    include_user=False,
    include_project=False,
    overrides={"profiles": {"codex-default": {"model": "your-model"}}},
)
profile = load_profile(
    "codex-default", config_file=app_config, include_user=False,
    overrides={"model": "your-model"},
)
```

```shell
# 共有設定を使用（CWD 設定を省略）
tkn-genai-bridge config list --no-project-config

# アプリ専用設定だけを読み、設定確認とオフライン計画を実行
tkn-genai-bridge config list --config "C:/path/to/your_cli/bridge.yaml" --no-user-config --no-project-config --json
tkn-genai-bridge generate --config "C:/path/to/your_cli/bridge.yaml" --no-user-config --no-project-config --prompt-file examples/prompt.txt --schema-file examples/output.schema.json --dry-run
```

この構成では共有設定と CWD 設定が不正な YAML・未対応スキーマ・未知の項目を含んでいても影響しません。
それらのファイルにしか存在しないプロファイルは `unknown_profile` になります。
明示ファイルが存在しない場合は `missing_config`、読めない・不正な場合は `invalid_config`、
未対応のスキーマ版は `unsupported_schema` で停止し、共有設定にはフォールバックしません。
組み込みプロファイルは引き続き使用できます。

### マージと設定元の表示

入れ子のマッピングは再帰的に統合し、値と `null` は置き換えます。
プロバイダーを変更するときは、前のプロバイダー専用設定を `null` にするか、新しいプロファイルを作成してください。
`profiles` の名前は識別子であり、`azure-quality` のような名前を使えます。
設定プロパティ自体は `snake_case` です。

`config list` は解決後の設定、選択中のプロファイル（`settings.default_profile`）、
読み込んだ設定元と各ファイルのスキーマ版、移行の有無（`sources`）、
各項目を採用した設定元（`field_sources`）を表示します。
有効なスキーマ版は `settings.schema_version`、通常の設定ファイルの場所は `user_config_path` です。
`user_config_path` は標準の保存先の案内であり、読み込みの有無を示しません。
省略した設定層は `sources` と `field_sources` に現れません。
専用設定だけの場合の `sources` は `built-in`、`explicit`、上書きがあれば `options` の順です。
`field_sources` は各フィールドの採用元を示し、外部ファイルに省略された既定値は `built-in` のままです。

既定の標準出力は `git config --list` のような1行1項目の `key=value` です。
入れ子のマッピングはドット、配列は `[0]` などの添字で表し、空の配列・マッピングは `[]`・`{}`、
真偽値は `true` / `false`、未設定値は `null` と表示します。
文字列にJSONの引用符を付けず、Windowsパスのバックスラッシュも二重にしません。
改行・タブなどの制御文字はエスケープして1行に収めます。

```text
settings.schema_version=1.1.0
settings.default_profile=codex-default
settings.profiles.codex-default.model=null
settings.profiles.codex-default.local_only=false
settings.profiles.codex-default.pricing={}
sources[0].name=built-in
sources[0].migrated=false
field_sources.profiles.codex-default.model=built-in
```

構造化した結果が必要な場合は `tkn-genai-bridge config list --json` を使います。
JSONの構造は `settings`、`user_config_path`、`sources`、`field_sources` です。
どちらの形式でも設定ファイル・state・cache・reportを作成・更新せず、認証・通信・外部CLI起動を行いません。
確認ログはINFOレベルで標準エラーへ出力し、`--quiet` をコマンドの前後に付けると抑制できます。
設定作成・失敗時にも共通の [CLI出力仕様](cli-output.md) を適用します。
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
CLIでは `--reasoning-effort high`、Pythonでは `load_profile("codex-default", overrides={"reasoning_effort": "high"})` で、その呼び出しだけ上書きできます。
要求した値は `GenerationPlan.reasoning_effort` と `GenerationResult.record.reasoning_effort`（生成失敗時は `GenAIError.record.reasoning_effort`）にも返します。
未指定は `null` であり、接続先が実際に採用したEffort levelの報告ではありません。
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

Windows の GitHub Copilot は、PATH で検出した npm の `copilot.cmd` / `copilot.ps1` から
ネイティブ本体を自動解決します。`cli.executable` の指定が優先され、有効なネイティブ実行ファイルや
PATH 上の `.exe` はそのまま使用します。明示指定した npm shim にも同じ解決を適用します。
`default_profile: copilot-default`、`provider: github-copilot`、`model: null` の設定でも、
`cli.executable` に絶対パスを追加する必要はありません。

対応する npm 構成は、global prefix 直下の shim とローカル `node_modules\.bin` の shim です。
検出位置を基準に `node_modules\@github\copilot` のパッケージ情報を確認し、
Windows のアーキテクチャに対応する `@github/copilot-win32-x64` または
`@github/copilot-win32-arm64` の `copilot.exe` を探します（x86 は未対応）。
本体パッケージは Copilot 配下の `node_modules`、または同じ `@github` 配下への hoist に対応します。
Copilot CLI 1.0.94 の x64 / ARM64 パッケージ情報で構成を確認しています。
shim の内容や `npm-loader.js` は実行・評価せず、本体を `shell=False` で直接起動します。
未知の shim・パッケージ構成や本体不在では、検出したパス、停止理由、
`cli.executable` による指定方法をエラーに表示します。
必要に応じて npm の optional dependencies を有効にして `@github/copilot` を再インストールするか、
利用可能な `copilot.exe` を指定してください。別のプロバイダーへ自動で切り替えることはありません。

### Ollama

組み込みの `local-vision` は画像対応モデル `qwen3.5:9b` を指定します。
`local_only: true`、`timeout_seconds: 300`、`max_output_tokens: 2048`、
`ollama.think: false`、`ollama.context_tokens: 16384` が初期値です。
モデルの取得やOllamaの起動は利用者が行います。`local` は従来どおり任意のローカルモデル用です。
モデルを変更するときは、Ollama側でも画像入力に対応していることを確認してください。

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
各単価の設定元は `config list` の `field_sources` で確認できます。
`pricing: {}` は上位設定にある単価の削除にはなりません。価格を切り離す場合は別プロファイルを使用してください。
