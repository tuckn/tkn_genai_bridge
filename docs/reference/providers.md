# プロバイダー接続仕様

本パッケージは接続先ごとの設定を共通APIに変換します。
各製品の認証、利用枠、モデルの可用性は統合しません。
以下の公式仕様とローカル実装を2026年9月22日に確認しました。
実サービスへの生成要求は実行していません。

## LiteLLM Python SDK

Ollama・Azure OpenAIは本パッケージ内の `LiteLLMBackend` から `litellm.completion()` を呼び出します。
外部CLIの起動はBridgeのCLIアダプターが担当します。
LiteLLM公式の [Claude Code連携](https://docs.litellm.ai/docs/proxy/client_setup/claude_code) は、
Claude CodeからLiteLLMのAPIゲートウェイへ接続する方式で、BridgeからCLIを起動する方式とは別です。
Ollamaは `ollama_chat`、Azure OpenAIは `azure` アダプターを使用します。
Azure v1用のOpenAIクライアントと、接続先を制御したhttpxクライアントを明示的に渡します。
生成要求の組み立てと送信はSDKに任せ、共有設定、認証、ローカル判定、応答の妥当性は本パッケージが担当します。
LiteLLM Proxy・Router・常駐サービスは使用しません。

LiteLLMは1.102系を対象とし、`uv.lock` では検証済みの1.102.0を固定しています。
SDKの更新時は通信形式と副作用の回帰テストを実行してください。
SDK固有のimport・要求オプション・内部HTTPHandlerへの依存は `providers/litellm.py` 内に限定します。
更新検証には `tests/test_litellm.py` の実SDK・外部通信拒否テストと、
`tests/test_failure_metadata.py` の成功応答を受理できない場合の情報保持テストを含めます。
CLIアダプターや `plan()`、設定コマンドではSDKをimportしません。

API生成時には次の方針で初期化します。

- `LITELLM_LOCAL_MODEL_COST_MAP=True` で価格表の外部取得を止めます。
- `LITELLM_MODE=PRODUCTION` でSDKによる `.env` の暗黙読み込みを止めます。
- `CUSTOM_TIKTOKEN_CACHE_DIR` を解除し、SDK同梱のトークナイザーを使います。
- `LITELLM_LOG=ERROR`、SDKロガーの無効化、メッセージログ・テレメトリの無効化、要求ごとの `no-log` を設定します。
- 要求ごとの再試行回数は0、キャッシュは無効、未対応のパラメーターは削除せずエラーにします。
- SDK全体にコールバック・モデル別名・フォールバックが登録されていれば、`sdk_configuration` で停止します。
- OllamaモデルをSDKのメタデータへ登録し、SDK独自の追加モデル照会を抑えます。生成結果のキャッシュではありません。

これらは一部がプロセス全体の環境変数・SDK設定に作用します。
利用側はLiteLLMを先にimport・設定せず、本パッケージに初期化を任せてください。
外部SDK設定を同じプロセスで併用するための隔離機能はありません。
アプリケーションの実行記録には `Runtime(observer=...)` を使えます。

SDKの正規化前に元のHTTP応答を確認します。
不完全な終了・拒否・ツール呼び出しを成功扱いにせず、応答にないモデル名や利用量を推測で補いません。
LiteLLMの価格計算や推定利用量は公開の `GenerationRecord` に含めません。
HTTPエラーの状態コードと `Retry-After` をSDKの例外変換前に取り出し、`ProviderError` に引き継ぎます。
Ollamaの事前確認も同じ契約です。秒数・HTTP日時を待機秒数へ変換し、不正値は不明にします。
自動待機・再試行は行いません。仕様は [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html#section-10.2.3) と
[Python API](api.md#例外) を参照してください。

公式資料: [LiteLLM SDK](https://docs.litellm.ai/docs/)、[Ollama](https://docs.litellm.ai/docs/providers/ollama)、[Azure](https://docs.litellm.ai/docs/providers/azure)。

## CLI接続

CLIはシェルを経由せず、引数配列とUTF-8標準入力で起動します。
利用側の作業ディレクトリを渡さず、実行専用の一時ディレクトリを使います。
プロンプトをプロセスの引数へ含めません。
Claude Code のスキーマは製品仕様に合わせて引数へ渡すため、OSのコマンド長制限を受けます。
Claude Code 2.1.282でルートのDraft 2020-12宣言が拒否されることを確認したため、
CLIへ渡すコピーからルートの `$schema` だけを省略します。
元のスキーマ・ハッシュ・Bridge側のDraft 2020-12検証は維持します。
他のDraft向けにキーワードを変換するものではなく、CLIが対応しない制約は引き続き失敗する場合があります。
参考: [Claude Codeの互換性報告](https://github.com/anthropics/claude-code/issues/80402)。

| 接続先 | 主な制御 | 出力・情報 |
| --- | --- | --- |
| Codex | `exec`、`--ephemeral`、`--ignore-user-config`、`--sandbox read-only`、`--output-schema` | 最終出力ファイル、JSONLの利用量 |
| Claude Code | `-p`、`--json-schema`、`--tools ""`、`--permission-mode dontAsk`、MCP設定制限、セッション保存無効 | `structured_output`、usage、取得できるモデル情報 |
| GitHub Copilot | silentモード、標準入力、組み込みMCPとカスタム指示の無効化、read/write/shell/url/memory拒否 | JSON本文。実モデル・利用量は不明 |
| Google Antigravity | `agy`、入出力 `stream-json`、`--json-schema`、`--disable-slash-commands`、`--mode plan`、`--sandbox` | 最終resultの `structured_output` とusage。実モデルは不明 |

Codex のユーザー設定は読みませんが、認証には通常の CODEX_HOME を使います。
Claude Code は一時フォルダの project 設定だけを読みます。
Copilot と Antigravity のユーザー設定由来のMCP・拡張など、製品側の機能を完全に隔離する契約はありません。
Antigravity は通常のユーザー認証・設定を使い、ツールを完全には無効化しません。
計画モード・サンドボックスの制限は agy が適用し、Bridgeはユーザーの権限設定を変更しません。
個人情報のローカル限定処理には、`local_only` を指定した Ollama を使ってください。

Codexの `turn.completed.usage` は各ターンの報告として集計します。
開始・完了イベントが対応しない場合や、後続の失敗・壊れたJSONL・非ゼロ終了・タイムアウトでは総量を確定せず、
取得できた数値を `Usage.known_subtotal` に残します。受信バッファの再取得で二重加算しません。
利用量の定義は [Codex SDKのイベント型](https://github.com/openai/codex/blob/main/sdk/typescript/src/events.ts)、
公開する総量・小計の契約は [コスト概算](costs.md#総量既知小計完全性) を参照してください。

AntigravityにはNDJSONのuserイベントを1件だけ標準入力で渡し、EOFで終了させます。
JSON Schemaは一時ファイルで渡し、単一の最終resultが `status: SUCCESS` かつ
`structured_output` がオブジェクトの場合だけ共通のスキーマ検証へ進みます。
最終resultのusageはセッション累計なので、途中のstep利用量とは加算しません。
`cache_read_tokens` と `thinking_tokens` を共通のキャッシュ読取・推論tokenへ対応付けます。
非ゼロ終了・タイムアウト・不正なストリーム・失敗statusでは、最終resultから読めた利用量を既知小計として保持します。
最終resultを受信していなければ利用量は不明です。
initイベントのmodelは指定値の反映なので、実モデル名として記録しません。
agyの内部待機は `--print-timeout 0s` にし、Bridgeの `timeout_seconds` でプロセスを制限します。
`--log-file` は一時ディレクトリ内を指定しますが、agy自身による会話・キャッシュ等の保存は製品側の管理です。

Windowsではタイムアウト時に起動したプロセスのPIDを指定して子プロセスも終了させます。
Linuxでは実行専用のプロセスグループを終了させますが、実動作は未検証です。
外部CLI自身が記録するログやキャッシュまで無保存を保証するものではありません。

導入済みCLIがこれらのオプションを持たない場合は、対応するCLIへ更新してください。
保護オプションを外して自動的に再実行することはありません。
Codex・Claude Code・Antigravityのローカルヘルプを確認しています。
2026-09-25に、同梱の匿名サンプルでWindowsから実接続を確認しました。
Codex（既定モデルとモデル指定）・Claude Code・GitHub Copilot・Antigravity・Ollama・Azure OpenAIは生成とJSON Schema検証に成功しています。
Claude CodeはBridge 0.7.1のスキーマ互換修正と再ログイン後に、同じ匿名サンプルで実生成を確認しました。
これは確認時点の環境・認証状態での結果です。通常のpytestは模擬応答を使い、外部サービスへの生成要求やクレジット消費を行いません。

公式資料: [Codex非対話実行](https://learn.chatgpt.com/docs/developer-commands#codex-exec)、[Claude Code CLI](https://code.claude.com/docs/en/cli-reference)、[GitHub Copilot CLI](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference)、[Antigravity headless mode](https://antigravity.google/docs/cli/headless/)。

### Claude Codeのログインと動作確認

Bridgeは `claude -p` による非対話実行でClaude Codeを呼び出します。
claude.aiアカウントで使う場合は、Claude Code CLIをインストールし、Bridgeと同じWindowsユーザーでログインしてください。
共有設定には `provider: claude-code` のプロファイルが必要です。同梱設定例の名前は `claude-default` です。
以前に作成した設定にこのプロファイルがなければ、[設定例](configuration.md#6種類のプロファイルを定義する例) を参考に、既存の `profiles` へ追加します。

#### 初回ログインと再ログイン

自分で操作できるPowerShellで実行します。

```powershell
claude auth login --claudeai
claude auth status
```

開いたブラウザーで利用するclaude.aiアカウントにログインします。
認証コードが表示された場合は、そのログインコマンドが待機しているPowerShellに貼り付け、ログイン完了を確認してください。
認証コードやトークンをチャット、BridgeのYAML、リポジトリへ貼り付けないでください。

認証情報はClaude Codeが管理します。Windowsでは通常 `%USERPROFILE%/.claude/.credentials.json` を使い、
`CLAUDE_CONFIG_DIR` を設定している場合はそのディレクトリを使います。
Bridgeがこのファイルを編集したり、独自のClaude認証情報を保存したりすることはありません。
保存済みの認証で正常に生成できていれば、生成のたびにログインし直す必要はありません。
認証方式や保存先の詳細は [Claude Code公式の認証ガイド](https://code.claude.com/docs/en/authentication) を参照してください。

#### Bridgeから動作確認する

リポジトリの匿名サンプルを使用します。以下はclaude-defaultが設定済みの場合の例です。

```powershell
cd "C:\path\to\tkn_genai_bridge"
tkn-genai-bridge generate --no-project-config --profile claude-default --prompt-file examples/prompt.txt --schema-file examples/output.schema.json --dry-run
```

dry-runは設定・入力・実行ファイルの存在を確認するだけで、ログインの有効性や生成は確認しません。
認証を含めて確かめる場合は、次の通常実行を1回行います。**サービスの利用枠・クレジットを消費する場合があります。**

```powershell
tkn-genai-bridge generate --no-project-config --profile claude-default --prompt-file examples/prompt.txt --schema-file examples/output.schema.json
```

終了コードが `0`、結果の `record.status` が `succeeded` で、`data.summary` に要約があれば、
認証・生成・元のJSON Schemaでの検証まで成功しています。
`claude auth status` の `loggedIn: true` や対話画面の表示だけでは、この確認の代わりになりません。

#### 生成に失敗する場合

Bridgeの `process_exit` はCLIが異常終了したことを示すため、これだけで認証失敗とは断定できません。
必要に応じて、同じPowerShellからClaude単体の短い非対話生成を試します。この確認も利用枠を消費する場合があります。

```powershell
claude -p "Reply only OK." --tools "" --no-session-persistence
```

- **Claude側でHTTP 401や `OAuth access token is invalid` が表示される**: 保存済み認証が拒否されています。上記のログインコマンドで認証を更新し、Bridgeのサンプルを再実行してください。
- **対話CLIでは返答を受け取れるがBridgeでは失敗する**: 同じ実行ファイル・Windowsユーザー・`CLAUDE_CONFIG_DIR` を使っているか確認してください。Bridgeは一時ディレクトリで起動し、`--setting-sources project` を使うため、通常起動のユーザー設定とは条件が異なります。
- **`--json-schema` がDraft 2020-12宣言を拒否する**: 認証とは別の互換性問題です。Bridge 0.7.1以降の修正を含むソースで `uv tool install . --reinstall` を実行し、`tkn-genai-bridge --version` で確認してください。利用側アプリから呼ぶ場合は、その環境のBridgeも更新します。

## Ollama

`POST /api/chat` に `stream: false`、`format: <JSON Schema>` を送ります。
`think` は設定された値をそのまま送り、別の推論レベルから推測で変換しません。
終了状態、本文、JSON構造を確認します。

`local_only` が有効なら、プロンプトを送る前に `POST /api/show` でモデル情報を確認します。
remote_model / remote_host がある場合、またはローカルの model_info が確認できない場合は停止します。
モデルの取得・更新・削除は行いません。

本パッケージのループバック制限だけでは、Ollamaサーバーのクラウド転送を遮断できません。
ローカル限定の運用では、Ollama側で `OLLAMA_NO_CLOUD=1` または `disable_ollama_cloud: true` を設定し、再起動してください。
パッケージがサーバーの設定を書き換えることはありません。

公式資料: [Chat API](https://docs.ollama.com/api/chat)、[ローカル限定モード](https://docs.ollama.com/faq#how-do-i-disable-ollama-cloud-features)。

## Azure OpenAI

`POST <endpoint>/chat/completions` を使います。
`endpoint` は `https://<resource>.openai.azure.com/openai/v1` のように指定し、モデル名にはデプロイ名を渡します。
出力形式は `json_schema`、`strict: true`、`store: false`、`stream: false` です。

認証は環境変数のAPIキー、Azure IdentityによるEntra ID認証、利用側コールバックから選べます。
Entra ID認証オブジェクトはRuntime単位で遅延作成し、連続生成で再利用します。
`get_token()` を生成ごとに呼び、SDKのメモリー内キャッシュと更新処理を使います。
`with Runtime(...)` または `close()` で終了処理を行ってください。
Bridgeは新たな永続トークンキャッシュを設定しません。別Runtime・別プロセス間の認証共有は行いません。
APIキーは生成ごとに環境変数から取得し、`token_provider` も生成ごとに呼び出します。
認証キャッシュと解放の仕様は [Azure Identity公式](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.interactivebrowsercredential?view=azure-python) を参照してください。
`store: false` はこの要求の保存オプションであり、サービス全体の保持方針を保証しません。

`finish_reason: stop`、拒否なし、ツール呼び出しなし、テキスト本文ありを成功応答の条件にします。
その後に共通のJSON・スキーマ検証を行います。
検証前に取得したモデル名・利用量は、失敗時も実行記録へ引き継ぎます。
URLリダイレクトは追跡しません。
システムのHTTPSプロキシ設定は使用します。

公式資料: [Azure OpenAI v1 Chat API](https://learn.microsoft.com/en-us/rest/api/microsoft-foundry/azureopenai/chat)。

## 依存関係とライセンス

直接依存は LiteLLM SDK（MIT）、OpenAI Python SDK（Apache-2.0）、httpx（BSD-3-Clause）、jsonschema（MIT）、referencing（MIT）、Pydantic（MIT）、PyYAML（MIT）です。
Entra認証用の azure-identity（MIT）も標準の直接依存に含みます。
本パッケージは [MIT](../../LICENSE) で配布します。
外部CLIやモデルをこのパッケージに同梱しないため、それらの導入・使用には各製品の条件が適用されます。
