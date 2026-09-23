# Python API

## 生成とオフライン計画

`Runtime(profile)` は設定を検証しますが、コンストラクターでは通信・認証・書き込みを行いません。
`GenerationRequest(prompt=..., output_schema=..., schema_name=...)` を渡して使います。

| 操作 | 動作 |
| --- | --- |
| `runtime.plan(request)` | プロンプトとスキーマを検証し、ハッシュと予定を返す |
| `runtime.plan(request, check_executable=True)` | CLIの実行ファイル確認も追加。プロセスは起動しない |
| `runtime.generate(request)` | 生成を1回要求し、出力を検証して返す |

`plan()` は生成、認証、モデル確認、書き込みを行いません。
APIの認証情報やモデルの利用可否までは確認できません。
LiteLLM SDKの読み込みも行いません。
`generate()` は同期処理です。
1回の呼び出しで外部CLIやサービスが内部的に何回のモデル呼び出しを行うかは、接続先が管理します。

## 入力の契約

プロンプトは空白だけを許さない文字列です。
JSON Schema は root の `type: object` が必須です。
`schema_name` は英数字・アンダースコア・ハイフンの1〜64文字、既定値は `generated_output` です。

スキーマの方言は Draft 2020-12 です。
`$schema` を書く場合は `https://json-schema.org/draft/2020-12/schema` を使ってください。
ローカルの `#/$defs/...` やアンカー参照を使用できます。
外部参照、`$id`、動的参照は拒否し、スキーマ検証のための外部取得を行いません。

出力は JSON オブジェクト1つに限定します。
Markdown の囲み、説明文、重複キー、NaN、Infinity はエラーです。
生成された値は元のスキーマで検証し、スキーマを緩めて成功扱いにはしません。
`format` はインストール済み jsonschema の FormatChecker が提供する検証を適用します。
追加ライブラリを要する format の保証が必要なら、利用側でも検証してください。

## 戻り値と実行記録

`GenerationResult.data` が検証済みのオブジェクトです。
`GenerationResult.record` は以下の情報を持ちます。

| フィールド | 意味 |
| --- | --- |
| `provider` | 接続先の識別子 |
| `requested_model` | 設定したモデル名。Azureではデプロイ名 |
| `response_model` | 応答に含まれるモデル名。未取得は `null` |
| `usage` | 入力・出力・キャッシュ入力・推論トークン数 |
| `started_at` | UTCオフセット付きの開始時刻 |
| `duration_seconds` | この呼び出しの実測経過秒数 |
| `status` | `succeeded` または `failed` |
| `error_code` | 失敗の分類。成功時は `null` |
| `prompt_sha256` | 入力プロンプトのUTF-8 SHA-256 |
| `schema_sha256` | キー整列したJSON表現のSHA-256 |

利用量が得られないフィールドは `null` であり、ゼロではありません。
LiteLLMが補完するモデル名・推定トークン数は使用せず、接続先の元の応答にある情報だけを記録します。
プロバイダーによってトークンの集計範囲が異なるため、単純に合算して料金とみなさないでください。
料金計算、処理全体の上限、複数プロセス間の予算管理は利用側の担当です。

`Runtime(profile, observer=callback)` で完了記録を受け取れます。
入力検証に失敗した場合は生成前なので通知しません。
プロバイダー呼び出し後の成功または想定済みの失敗について1回通知します。
コールバックの例外は警告にとどめ、取得済みの成功結果を失敗へ変えません。
保存が必須のアプリは戻り値を受け取って自分で保存・検証してください。

## 例外

`GenAIError` が共通の基底クラスです。

| 型 | 主な原因 |
| --- | --- |
| `ConfigError` | 不正な設定、見つからないプロファイル、既存設定との競合 |
| `RequestError` | 不正なプロンプト／スキーマ |
| `ProviderError` | 認証、実行ファイル、タイムアウト、HTTPエラー、未完了応答 |
| `OutputValidationError` | JSONの解析やスキーマ検証の失敗 |

各例外に `code` があり、呼び出し後の失敗には `record` が付きます。
`ProviderError.retryable` は一時的なHTTP障害かの参考値です。
自動再試行は実行しません。
`submission_unknown` はプロセスや通信の失敗により送信・課金結果が不明な場合に `true` です。
再試行は利用側で重複・課金を判断し、回数を制限してください。

`Profile` や `GenerationRequest` の構築時に型が誤っている場合は Pydantic の `ValidationError` です。
構築エラーを記録する際は、Pydanticのエラーに含まれる入力値をそのまま公開ログへ出さないでください。
ライブラリ内部の想定外のプログラミングエラーは握りつぶしません。
ただし、外部SDKの例外には応答本文が含まれるため、SDK境界では内容を除いた `ProviderError` に変換します。
`sdk_error` はSDK内部または未対応の要求形式、`sdk_configuration` はSDKの全体設定との競合です。
詳細な初期化方針は [接続仕様](providers.md#litellm-python-sdk) を参照してください。

## 拡張と保存

`Runtime(..., backend=...)` には `providers.base.Backend` に合うアダプターを渡せます。
この注入機能はテストや利用側が管理する接続向けです。
独自アダプターは通信制限などを自ら実装してください。

パッケージは生成結果や実行記録を永続保存しません。
プロンプト本文・認証情報・応答本文をエラーメッセージに含めません。
呼び出し元が作るログ、標準出力に返す成功データ、外部CLIやSDK自身のログは別の管理対象です。
