# Python API

## 生成とオフライン計画

`Runtime(profile)` は設定を検証しますが、コンストラクターでは通信・認証・書き込みを行いません。
`GenerationRequest(prompt=..., output_schema=..., schema_name=...)` を渡して使います。

| 操作 | 動作 |
| --- | --- |
| `runtime.plan(request)` | 入力を検証し、ハッシュ・token概算・参考額を返す |
| `runtime.plan(request, output_tokens=1000)` | 出力tokenの仮定値を指定。生成条件・上限の設定は変更しない |
| `estimate_tokens(request, profile, output_tokens=1000)` | 入力済みリクエストの粗いtoken概算。完全な入力検証には `plan()` を使用 |
| `estimate_cost(usage, pricing)` | token数と参考単価から再計算。生成を呼び出さない |
| `runtime.plan(request, check_executable=True)` | CLIの実行ファイル確認も追加。プロセスは起動しない |
| `runtime.generate(request)` | 生成を1回要求し、出力を検証して返す |
| `runtime.close()` | Runtimeが所有する認証オブジェクトを解放。同じRuntimeを再利用しない |

`plan()` は生成、認証、モデル確認、書き込みを行いません。
APIの認証情報やモデルの利用可否までは確認できません。
LiteLLM SDKの読み込みも行いません。
`generate()` は同期処理です。
同じRuntimeの並列呼び出しには対応しません。
1回の呼び出しで外部CLIやサービスが内部的に何回のモデル呼び出しを行うかは、接続先が管理します。

連続した生成では `with Runtime(profile) as runtime:` の内側で `generate()` を繰り返します。
Azure認証オブジェクトは最初の生成で作られ、同じRuntime内で再利用されます。
トークン取得は毎回認証オブジェクトに委ね、有効期限・更新をSDKに処理させます。
`with` を抜けると成功・失敗にかかわらず解放します。`close()` は複数回呼んでも問題ありません。
解放後の `plan()` / `generate()` / コンテキストへの再入場は `runtime_closed` で停止します。
補助CLIと付属サンプルはこの終了処理を行います。

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
| `usage` | 入力・出力・キャッシュ入力・キャッシュ書込・推論token数と入力の集計範囲 |
| `cost_estimate` | 参考額・通貨・基準日付き単価・計算に使ったusage。旧記録の未記載は `null` |
| `started_at` | UTCオフセット付きの開始時刻 |
| `duration_seconds` | この呼び出しの実測経過秒数 |
| `status` | `succeeded` または `failed` |
| `error_code` | 失敗の分類。成功時は `null` |
| `prompt_sha256` | 入力プロンプトのUTF-8 SHA-256 |
| `schema_sha256` | キー整列したJSON表現のSHA-256 |
| `bridge_version` | 生成に使用したBridgeのバージョン |
| `profile_name` | 選択した設定プロファイル名。直接構築して名前未指定なら `null` |
| `generation_settings_sha256` | 認証情報を除いた生成条件のSHA-256 |

この3項目は `GenerationPlan` にも含まれます。
`load_profile()` / `load_config().profile()` で得た `Profile.profile_name` は、設定の上書き後も保持します。
名前は設定項目には追加されず、プロファイルの `model_dump()` には含めません。
直接作成したプロファイルでは `Runtime(profile, profile_name="app-profile")` で任意の識別名を指定できます。
0.3以前の記録を `GenerationRecord` へ読み込むと、未記録の3項目は `null` です。

生成条件のハッシュには、接続先の種類、要求モデル、推論設定、タイムアウト、出力トークン上限、
`local_only`、`schema_name` と、該当するCLI実行ファイル指定／Ollama設定／Azure endpointを含めます。
キー順に依存せず、省略したCLI・Ollama設定は既定値へ展開してハッシュ化します。
プロファイル名、認証方式、環境変数名・値、テナント、トークンスコープ、認証コールバックは含めません。
参考単価も生成条件のハッシュには含めません。単価更新だけで再生成する必要はなく、計算時の単価は概算結果に保存します。
プロンプトとスキーマ本文は既存の2つのハッシュで識別します。
実行ファイルやendpointの生の値は記録に追加しません。

再生成判定には、Bridge版と3つのハッシュを併せて比較してください。
同じ値でも生成結果の一致を保証しません。CLIの自動モデル選択・実行ファイルの中身、サーバー側のモデル変更、
認証先の権限差、独自backendの内部設定は、このハッシュだけでは検出できません。

利用量が得られないフィールドは `null` であり、ゼロではありません。
JSON解析・スキーマ不一致・途中終了・拒否・本文欠落などで失敗しても、
解析できた応答にある利用量とモデル情報を失敗記録へ保持します。
CodexとClaude Codeでは、非ゼロ終了時も標準出力から読み取れる情報を保持します。
応答全体が不正なJSONの場合や、応答を取得できなかった通信失敗では情報を推測しません。
LiteLLMが補完するモデル名・推定トークン数は使用せず、接続先の元の応答にある情報だけを記録します。
`Usage.input_tokens_scope` は通常 `total`（キャッシュ分を含む）で、Claude Codeは `uncached` です。
Claudeの入力値を改変せず、コスト計算時にキャッシュ読込・書込を足して全入力を扱います。
必要なキャッシュ数が取得できなければ金額も不明です。推論tokenは出力に含まれるため再加算しません。
失敗時の記録にも取得済みusageから参考額を計算します。通信断などの未取得分を事前概算で埋めません。

参考額の計算はBridge、処理全体の上限・実行可否・複数プロセス間の予算管理は利用側の担当です。
詳しい単価・欠測・キャッシュの契約は [コスト概算](costs.md) にあります。
0.4以前のClaude記録を再計算するときは、`input_tokens_scope="uncached"` を指定し、
元の記録から必要なキャッシュ数を補ってください。不明な数値を0として補完しないでください。

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
`backend=` で渡したオブジェクトと `token_provider=` は利用側の所有物であり、Runtimeは閉じません。
`LiteLLMBackend` を明示的に作成・注入する場合は利用側で `backend.close()` を実行してください。
独自アダプターは失敗時に `GenAIError.metadata = ResponseMetadata(response_model=..., usage=...)` を設定すると、
取得済み情報をRuntimeの失敗記録へ引き継げます。本文や認証情報をmetadataへ入れないでください。
公開モデル・例外はLiteLLM固有の型を要求しません。

パッケージは生成結果や実行記録を永続保存しません。
プロンプト本文・認証情報・応答本文をエラーメッセージに含めません。
呼び出し元が作るログ、標準出力に返す成功データ、外部CLIやSDK自身のログは別の管理対象です。
