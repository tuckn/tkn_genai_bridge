# CLIの結果表示と終了コード

## 表示形式と副作用

`config init`、`config list`、`generate`、`generate --dry-run` と、
引数・設定・要求・実行エラー、中断の結果は、既定で人向けの1行1項目の `key=value` 一覧です。
入れ子はドット、配列は `[0]` などの添字、空の配列・マッピングは `[]`・`{}` で表します。
文字列にJSONの引用符を付けず、Windowsパスのバックスラッシュを二重にせず、パスや値を切り詰めません。
キー・値内の改行、タブなどの制御文字はエスケープします。真偽値は `true` / `false`、未設定値は `null` です。

`--json` を付けると同じ結果データを1つの妥当なJSON documentとしてstdoutへ出します。
非有限数を許す独自JSONやログ・見出し・ANSIカラーは出力しません。
JSON文字列内のパスのバックスラッシュはJSON仕様に従ってエスケープされ、JSON解析後は元の文字列です。
`--json` はコマンドの前後、`config` と `init` / `list` の間に指定でき、繰り返しても指定を保持します。
`--` より後ろの文字列は表示形式の指定として扱いません。
`--help` と `--version` は `--json` があっても通常の案内を表示し、正常終了します。

進捗・警告・診断はstderrへ分離します。`--quiet` / `--verbose` はコマンドの前後に指定でき、
ログ量だけを変更します。同時指定は引数エラーです。最終結果を抑制せず、形式も変更しません。
TTY・パイプ・リダイレクトを理由に出力形式を自動変更しません。

全項目を両形式に表示します。大量のskipをまとめる処理はなく、`--full-output` は未実装です。
`generate` は単一要求を扱い、対象の画像情報は `images` / `record.images` 配列に指定順で残ります。
バッチの成功・失敗件数を新たに作ったり、一部情報を省略した成功と見なしたりしません。
途中で失敗した生成は非0で終了し、取得済みの記録・利用量だけを返します。

表示形式の選択は、生成・書き込み・上書き・認証・通信・課金の有無を変更しません。
`config init` は通常実行時に設定を作成し、`--dry-run` の場合だけ作成を予告します。
`config list` は読み取り専用です。`generate --dry-run` は通信・認証・AI呼び出し・永続書き込みを行いません。
`generate` の通常実行は接続先を呼び出し、課金される場合があります。生成結果の自動保存は行いません。

## 結果データ

JSONの型・キーを機械処理用の契約とします。通常一覧は同じデータを平坦化した表示です。
出力形式によってフィールドや型を変更しません。

| コマンド／経路 | 主なフィールドと型 |
| --- | --- |
| `config init` | `status: string`（`created` / `unchanged` / `would_create`）、`path: string`（絶対パス） |
| `config list` | `settings: object`、`user_config_path: string`、`sources: array`、`field_sources: object` |
| `generate --dry-run` | `provider: string`、`model: string \| null`、`profile_name: string \| null`、`will_call_provider: false`、ハッシュ・token概算・参考額・画像メタデータ |
| `generate` | `data: object`（要求スキーマで検証済み）、`record: object`（`status: succeeded`、利用量・実行情報） |
| 失敗 | `error: object`。`code: string`、`message: string`、必要に応じ `paths: object` |
| provider失敗 | 上記に `http_status: integer \| null`、`retry_after_seconds: number \| null`、`retryable: boolean`、`submission_unknown: boolean` |
| 取得済み失敗記録 | `record: object`。`status: failed`、`error_code` と取得済みのモデル・利用量を保持 |

詳細なplan・recordのフィールドは [Python API](api.md)、設定の構造は [設定仕様](configuration.md) を参照してください。
取得できなかった利用量やモデルは推測しません。診断本文、認証情報、入力プロンプトを失敗結果へ追加しません。
生成した `data` は利用者が要求した内容です。結果を共有する場合はその内容を確認してください。

### 失敗と確認対象パス

`error.message` は理由、`error.paths` は確認対象パスを返します。エラー文章を解析してパスを抜き出す必要はありません。

- 入力ファイルの読込エラー: `prompt_file` または `schema_file: string`。
- スキーマJSON不正: `schema_file: string`。
- 画像の読込・形式エラー: `image: string`。
- 実行・入力モデル検証: `prompt_file`、`schema_file`、`images: array[string]`。
- 設定作成: `config_file: string`。
- 設定読込: `user_config`、必要に応じ `project_config`、明示した `config_file: string`。
  これは読み込み候補の一覧であり、すべてが存在する／不正であるという意味ではありません。
- 引数解析失敗など、対象を確定できない場合は `paths` を付けません。

予期しない例外は `runtime_error` と内容を含まないメッセージを返し、秘密情報が含まれ得る例外本文・tracebackを出しません。
中断は `error.code=interrupted` です。送信後の要求が接続先で実行されたかどうかを断定せず、
自動再試行もしません。OSによる強制終了や出力先の切断など、結果を書き出せない状況は形式保証の対象外です。

## 終了コード

| コード | 意味 |
| --- | --- |
| `0` | 正常終了、検証成功、`created` / `unchanged` / `would_create` |
| `1` | 生成・provider・出力検証の失敗、`RequestError`、予期しない実行エラー |
| `2` | 引数・設定エラー（`ConfigError`）、入力モデルの `ValidationError` |
| `130` | `KeyboardInterrupt` による中断 |

スキーマファイルのJSON不正（`invalid_schema`）や画像要求エラーは `1`、
入力ファイルのUTF-8読込失敗（`input_io`）は `2` です。表示形式で成功・失敗の判定を変えません。
`main()` を直接使うCLIテストでも、引数エラーは結果を表示して `2` を返します。
`--help` / `--version` の案内にはargparseの通常の `SystemExit(0)` を維持します。

## 使用例

`--json` を付けると機械処理用JSONを表示します。パイプやリダイレクトの有無で形式は変わりません。

```shell
tkn-genai-bridge --json config init --dry-run
tkn-genai-bridge config --json list
tkn-genai-bridge generate --json --prompt-file examples/prompt.txt --schema-file examples/output.schema.json --dry-run
```

自動処理では終了コードと `error.code` を確認してから結果を使用してください。
Pythonからの呼び出し例は [READMEの「結果を機械処理に渡す」](../../README.md#結果を機械処理に渡す) を参照してください。

`--json` はstdoutの表示形式だけを選ぶオプションで、成果物を保存する用途はありません。
Python APIの戻り値、保存データ、外部CLIやHTTPの通信形式は、CLIの表示形式とは独立しています。
