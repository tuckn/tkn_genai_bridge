# 既存の Python CLI から切り出す

このガイドは `tkn_codex_chat_note_pipeline` の内部実装を共通パッケージへ段階的に切り替えるためのものです。
共通パッケージを追加しただけでは、既存CLIの動作や設定は変わりません。
利用側リポジトリの変更は別の移行作業として行ってください。

API接続にはLiteLLM SDKを使います。
利用側でLiteLLMを直接importしたり、Proxyを起動したりする必要はありません。

## 責務を対応させる

| 既存実装 | 移行先 |
| --- | --- |
| 接続先の振り分け、外部プロセス実行 | `Runtime.generate()` とCLIアダプター |
| Ollama / Azure のHTTP要求 | 共通パッケージ内のLiteLLM SDKアダプター |
| 接続先・モデル・認証の設定 | 共通の接続プロファイル |
| プロンプト、出力スキーマ、生成プロファイル | 利用側に保持 |
| event IDの短縮・復元、許可IDの列挙、出典検証 | 利用側に保持 |
| 入力トークン推定、料金表、処理全体の予算、分割・統合・修復 | 利用側に保持 |
| usage記録の既存形式、manifest、状態、Markdown保存 | 利用側で `GenerationRecord` を変換 |

既存の `api_inference.py` をそのまま移す必要はありません。
そこにはチャットイベントIDとノート固有の処理が含まれています。
特に料金上限・実行回数上限・再開の契約は、共通化を理由に削除しないでください。

## 移行の順序

1. 利用側の依存関係へ共通パッケージを追加し、バージョンを固定します。
2. 既存の接続設定を読み、同じ意味の共有プロファイルを明示的に作成します。
3. 利用側の設定に `genai_profile` などの選択項目を追加します。既存configの互換性と版を決めます。
4. 利用側のadapterだけを差し替え、既存の入力整形・予算確認・結果検証・保存を維持します。
5. provider別の匿名fixtureで結果、失敗、再開、usage情報を比較します。
6. 許可された小さな実データまたは匿名入力で実サービスを確認し、利用側CLIを再インストールします。

## 呼び出し境界の例

```python
from tkn_genai_bridge import GenerationRequest, Runtime, load_profile

# app_config、build_prompt、output_schema は利用側アプリで管理する値です。
profile = load_profile(app_config.genai_profile)
runtime = Runtime(profile)
request = GenerationRequest(
    prompt=build_prompt(source),
    output_schema=output_schema,
)
if dry_run:
    plan = runtime.plan(request)
else:
    # この直前に利用側の回数・料金上限を確認します。
    result = runtime.generate(request)
    # 元データとの対応を検証してから既存の保存処理へ渡します。
    validate_against_source(result.data, source)
    save_validated_output(result.data, result.record)
```

これは利用側への組み込み位置を示す例で、未定義の関数は利用側の処理に置き換えます。
動く最小例は [README](../../README.md#python-cli-に組み込む) にあります。

## 互換性上の確認点

- 共通APIは JSON本体だけでなく、`data` と `record` を返します。既存の戻り値が必要なら利用側で `result.data` を返すラッパーを置きます。
- 既存の `invoke_structured(..., cwd=..., timeout=...)` とは引数が異なります。タイムアウトはプロファイルに設定し、一時フォルダは共通パッケージが管理します。
- 既存のプロバイダー名は同じ識別子を使います。成果物の `generator` 表示名やFrontmatterは利用側で維持してください。
- Ollamaの `reasoning_effort` から `think` への推測変換はありません。使用モデルに合う `ollama.think` を明示してください。
- Azure向けに既存コードが行うスキーマ変換やイベントID変換は、必要性を見直しつつ利用側へ残します。共通パッケージは渡されたスキーマを緩めません。
- Azureの既存ブラウザー認証キャッシュは自動移動しません。継続利用する場合は既存認証関数を `token_provider` コールバックとして渡せます。
- 共有設定は `~/.tkn/genai/config.yaml` です。既存のアプリ設定を同じ場所へコピーしないでください。
- usageが取得できなかった場合をゼロとして扱わないでください。失敗時の記録は `GenAIError.record` から取得できます。
- 自動再試行・プロバイダー切り替えはありません。利用側の再試行は回数、失敗条件、課金の可能性を明示して維持します。
