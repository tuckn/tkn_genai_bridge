# プロバイダー接続仕様

本パッケージは接続先ごとの設定を共通APIに変換します。
各製品の認証、利用枠、モデルの可用性は統合しません。
以下の公式仕様とローカル実装を2026年9月22日に確認しました。
実サービスへの生成要求は実行していません。

## LiteLLM Python SDK

Ollama・Azure OpenAIは本パッケージ内の `LiteLLMBackend` から `litellm.completion()` を呼び出します。
Ollamaは `ollama_chat`、Azure OpenAIは `azure` アダプターを使用します。
Azure v1用のOpenAIクライアントと、接続先を制御したhttpxクライアントを明示的に渡します。
生成要求の組み立てと送信はSDKに任せ、共有設定、認証、ローカル判定、応答の妥当性は本パッケージが担当します。
LiteLLM Proxy・Router・常駐サービスは使用しません。

LiteLLMは1.102系を対象とし、`uv.lock` では検証済みの1.102.0を固定しています。
SDKの更新時は通信形式と副作用の回帰テストを実行してください。
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

公式資料: [LiteLLM SDK](https://docs.litellm.ai/docs/)、[Ollama](https://docs.litellm.ai/docs/providers/ollama)、[Azure](https://docs.litellm.ai/docs/providers/azure)。

## CLI接続

CLIはシェルを経由せず、引数配列とUTF-8標準入力で起動します。
利用側の作業ディレクトリを渡さず、実行専用の一時ディレクトリを使います。
プロンプトをプロセスの引数へ含めません。
Claude Code のスキーマは製品仕様に合わせて引数へ渡すため、OSのコマンド長制限を受けます。

| 接続先 | 主な制御 | 出力・情報 |
| --- | --- | --- |
| Codex | `exec`、`--ephemeral`、`--ignore-user-config`、`--sandbox read-only`、`--output-schema` | 最終出力ファイル、JSONLの利用量 |
| Claude Code | `-p`、`--json-schema`、`--tools ""`、`--permission-mode dontAsk`、MCP設定制限、セッション保存無効 | `structured_output`、usage、取得できるモデル情報 |
| GitHub Copilot | silentモード、標準入力、組み込みMCPとカスタム指示の無効化、read/write/shell/url/memory拒否 | JSON本文。実モデル・利用量は不明 |

Codex のユーザー設定は読みませんが、認証には通常の CODEX_HOME を使います。
Claude Code は一時フォルダの project 設定だけを読みます。
Copilot のユーザー設定由来のMCP・拡張など、製品側の機能を完全に隔離する契約はありません。
個人情報のローカル限定処理には、`local_only` を指定した Ollama を使ってください。

Windowsではタイムアウト時に起動したプロセスのPIDを指定して子プロセスも終了させます。
Linuxでは実行専用のプロセスグループを終了させますが、実動作は未検証です。
外部CLI自身が記録するログやキャッシュまで無保存を保証するものではありません。

導入済みCLIがこれらのオプションを持たない場合は、対応するCLIへ更新してください。
保護オプションを外して自動的に再実行することはありません。
CodexとClaude Codeのローカルヘルプを確認しています。
Copilotは公式仕様と模擬実行で確認し、この環境の実行ファイルは動作確認できていません。

公式資料: [Codex非対話実行](https://learn.chatgpt.com/docs/developer-commands#codex-exec)、[Claude Code CLI](https://code.claude.com/docs/en/cli-reference)、[GitHub Copilot CLI](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference)。

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
`store: false` はこの要求の保存オプションであり、サービス全体の保持方針を保証しません。

`finish_reason: stop`、拒否なし、ツール呼び出しなし、テキスト本文ありを成功応答の条件にします。
その後に共通のJSON・スキーマ検証を行います。
URLリダイレクトは追跡しません。
システムのHTTPSプロキシ設定は使用します。

公式資料: [Azure OpenAI v1 Chat API](https://learn.microsoft.com/en-us/rest/api/microsoft-foundry/azureopenai/chat)。

## 依存関係とライセンス

直接依存は LiteLLM SDK（MIT）、OpenAI Python SDK（Apache-2.0）、httpx（BSD-3-Clause）、jsonschema（MIT）、referencing（MIT）、Pydantic（MIT）、PyYAML（MIT）です。
Entra認証用の azure-identity（MIT）も標準の直接依存に含みます。
本パッケージは [MIT](../../LICENSE) で配布します。
外部CLIやモデルをこのパッケージに同梱しないため、それらの導入・使用には各製品の条件が適用されます。
