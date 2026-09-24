# token・コスト概算

Bridgeはtoken数を基礎データとして保持し、利用者が設定した単価を適用します。
許容額、token・回数の上限、停止・続行、複数呼び出しの予約額や予算は利用側CLIが管理します。
参考額は請求額ではありません。サブスクリプション、ツール料金、税、為替、段階制・長文料金、
異なるモデルの混在やキャッシュ保持時間の違いは自動判定しません。用途ごとの単価を設定してください。
単価取得や更新のための通信は行いません。LiteLLMの価格表も計算には使用しません。

## 単価の設定

共有設定 `~/.tkn/genai_bridge/config.yaml` のプロファイルに追加します。
次の価格は説明用の架空値で、現在のサービス価格を示しません。

```yaml
schema_version: "1.1.0"
default_profile: azure-quality
profiles:
  azure-quality:
    provider: azure-openai
    model: your-deployment-name
    max_output_tokens: 16000
    azure:
      endpoint: https://your-resource.openai.azure.com/openai/v1
    pricing:
      your-deployment-name:
        currency: JPY
        pricing_date: "2026-01-01"
        input_per_million: 30.0
        output_per_million: 180.0
        cache_policy: no-cache
```

| 単価の項目 | 契約 |
| --- | --- |
| `currency` | 必須。`JPY` / `USD` など大文字3文字。換算しない |
| `pricing_date` | 必須。引用符付きの有効な `YYYY-MM-DD` |
| `input_per_million` | 必須。100万入力tokenあたりの参考額 |
| `output_per_million` | 必須。100万出力tokenあたりの参考額 |
| `cache_policy` | 既定 `no-cache`。全入力に通常単価。`observed` は取得済みのキャッシュ数を使用 |
| `cached_input_per_million` | キャッシュ読込の単価。`observed` では必須 |
| `cache_write_per_million` | 任意の書込単価。`observed` で省略すると書込も通常入力単価 |

価格は0以上の有限数です。0は明示的な参考単価として有効ですが、未設定を0とは解釈しません。
`pricing` のキーは要求モデル名の完全一致です。Azureではデプロイ名を使います。
指定モデルに単価がなければ他モデルの価格は流用しません。
CLIでモデルを指定しない場合、事前見積もりの金額は不明です。実行後に応答モデルが取得できた場合だけ、
その名前と一致する単価を使います。いずれも設定した単価シナリオの参考額です。

## 生成前の確認

```python
from tkn_genai_bridge import GenerationRequest, Runtime, load_profile

request = GenerationRequest(prompt="この文を要約してください。", output_schema={"type": "object"})
with Runtime(load_profile("azure-quality")) as runtime:
    plan = runtime.plan(request)
    print(plan.token_estimate.model_dump())
    print(plan.cost_estimate.model_dump())
    # 利用側CLIが金額・通貨・token数・不明時の方針を確認した後に generate(request) を呼ぶ。
```

`token_estimate.input_tokens` は、送信するプロンプト・JSON Schema・名前・構造化出力の枠組みを
JSON化したUTF-8バイト数に512を加えた粗い概算です。
Ollama/CopilotではBridgeが付加するJSON指示文も含み、Ollamaではスキーマを重ねて送る範囲も含めます。
`method` は `utf8-bytes-plus-margin-v1`、`input_tokens_source` は `builtin`、`margin_tokens` は512です。
モデル別の正確なtokenizerではありません。既存CLIのUTF-8による代替推定を基に、
環境内のtokenizerキャッシュの有無に依存しない計算にしています。
実token数との差は大きくなり得ます。プロバイダーの隠れた指示文、ツール呼び出しや内部再試行は含まれず、
厳密な上限でもありません。

独自tokenizerや既存CLIの入力推定値は、そのまま `plan()` に渡せます。

```python
with Runtime(load_profile("azure-quality")) as runtime:
    plan = runtime.plan(
        request,
        input_tokens=4200,  # 利用側が計算した、スキーマ・指示文・余裕分を含む最終値
        input_tokens_method="local-tokenizer-v1",
        output_tokens=1000,
    )
assert plan.token_estimate.input_tokens == 4200
assert plan.token_estimate.input_tokens_source == "caller"
assert plan.token_estimate.margin_tokens == 0
```

入力値は0以上の整数です。Bridgeは追加の余裕分を加算せず、指定値で参考額を計算します。
`input_tokens_method` は省略時 `caller-supplied`。英数字で始まる120文字以内の識別名で、
英数字・`.`・`_`・`:`・`/`・`+`・`-` を使えます。入力値なしで方法名だけを指定することはできません。
方法名は計画のJSONに残るため、プロンプトや個人のファイルパスを含めないでください。
`estimate_tokens()` でも同じ引数を使えます。生成条件・出力上限・生成条件のハッシュは変えません。
ローカルtokenizerの選択・実行は利用側で行い、Bridgeはcallbackの実行やtokenizerの取得を行いません。
料金だけ必要なら、従来どおり `Usage` と `estimate_cost()` を組み合わせられます。

出力は実行前には分からないため、`Profile.max_output_tokens` を仮定値として使います。
`plan(request, output_tokens=1000)` で仮定値だけを上書きできます。0以上の整数を指定してください。
`output_tokens_source` は `profile_limit` / `caller` / `unknown` です。
出力仮定値がなければ `output_tokens` と金額は `null` で、入力概算は返します。
CLIプロバイダーには出力上限を送れないため、必要なら仮定値を明示します。

事前の金額計算ではキャッシュ再利用を仮定しません。
`observed` で書込単価が通常入力単価より高ければ、入力の全量を書込と仮定します。
この仮定は `cost_estimate.usage` で確認できます。入力数自体の誤差や追加料金は保証しません。

補助CLIでも `generate --dry-run` で計算でき、`--estimate-output-tokens 1000` で仮定値を追加できます。
生成・認証・モデル問い合わせ・tokenizerのダウンロード・書き込みは行いません。
`--estimate-input-tokens 4200 --estimate-input-method local-tokenizer-v1` で入力推定値も指定できます。
3つの `--estimate-*` はdry-run専用です。価格や概算額による自動停止機能はありません。

## 生成後と再計算

`record.usage` はプロバイダーが返した値を保持します。
`record.cost_estimate` は成功時と、利用量を取得できた失敗時に同じ計算を行います。
observer・`GenAIError.record`・補助CLIのJSONにも含みます。
実績がなければ `reported` の金額は不明であり、事前の概算値と混ぜません。

```python
from tkn_genai_bridge import TokenPricing, Usage, estimate_cost

usage = Usage(input_tokens=60000, output_tokens=16000)
price = TokenPricing(
    currency="JPY", pricing_date="2026-01-01",
    input_per_million=30.0, output_per_million=180.0,
)
cost = estimate_cost(usage, price)
assert cost.amount == 4.68
# 保存済みrecord.usageを渡し、priceだけ変えれば生成なしで再計算できます。
```

| 戻り値の項目 | 意味 |
| --- | --- |
| `status` | `estimated` または `unavailable` |
| `amount` / `currency` | 参考額と通貨。計算不能時の金額は `null` |
| `basis` | `planned`（事前）、`reported`（取得済みusage）、`scenario`（直接の再計算） |
| `usage` | 計算に使ったtoken数と入力の範囲 |
| `pricing` | 計算時の単価・通貨・基準日・キャッシュ方針のスナップショット |
| `pricing_model` | 自動選択に使ったモデル／デプロイ名。直接の計算は省略可能 |
| `unavailable_reason` | 算出不能の理由。計算できた場合は `null` |

算出不能の理由は `pricing_not_configured`、`usage_missing`、`usage_incomplete`、`cache_usage_missing`、
`inconsistent_usage`（キャッシュ内訳が全入力より大きいなど）、`non_finite_cost` です。
欠測のある複数記録を合計する際、不明を0とみなすかをBridgeは決めません。
利用側で通貨を確認し、欠測件数を別途扱ってください。
内部の乗算・加算はDecimalで行い、最終的な金額はJSONで扱えるfloatにします。通貨の桁丸めは行いません。

## 総量・既知小計・完全性

`Usage` の直下のtoken数は呼び出し全体の総量です。`known_subtotal` は取得できた分のtoken数を持つ
`TokenCounts` で、いずれも未取得は `null`、報告された0は0のままです。

| `completeness` | 意味 |
| --- | --- |
| `complete` | 呼び出し全体をカバーする報告。項目単位で未報告の数値は `null` |
| `partial` | 途中失敗などで全体を確認できず、既知の数値がある。直下の総量はすべて `null` |
| `unknown` | 全体を確認できず、数値の報告もない |

Codexでは、開始・完了イベントで区切られた各ターンのusageを合計します。
後続ターンの失敗、未完了、壊れたJSONL、プロセスの非ゼロ終了・タイムアウトでは既知小計だけを残します。
開始イベントがない単独の完了報告も読み取れます。対応する開始がない重複完了を二重加算しません。
終了後の出力JSON・スキーマ検証が失敗しても、全体のusageを受信済みなら `complete` です。
API応答やClaude Codeの終端報告も、出力が受理できるかと利用量の完全性を分けて扱います。

```python
from tkn_genai_bridge import TokenCounts, Usage, estimate_cost

partial = Usage(
    completeness="partial",
    known_subtotal=TokenCounts(input_tokens=100, output_tokens=20),
)
assert partial.input_tokens is None
assert partial.known_subtotal.input_tokens == 100
assert estimate_cost(partial, price).unavailable_reason == "usage_incomplete"
```

`complete` でも、複数ターンのうち1つで出力tokenが欠測なら `output_tokens` は `null` です。
取得できた出力数だけを `known_subtotal.output_tokens` に保持します。
小計は項目ごとに集計対象が異なり得るため、小計を総量へ置き換えて金額を計算しないでください。
Bridgeは小計の金額を自動算出せず、部分利用量の全額は `usage_incomplete` とします。
単価も未設定の場合は `pricing_not_configured` が優先されます。
利用側は総額が不明であることと既知小計を別々に表示し、予算の予約を解放するかを判断します。

既存の `Usage(input_tokens=..., output_tokens=...)` は総量の明示指定として `complete` を補います。
旧記録の読み込みも同じ扱いですが、過去に失われた小計や完全性を復元できるわけではありません。
旧版で途中失敗した記録は、元データを確認してから再計算してください。

## キャッシュと推論token

通常の `Usage.input_tokens_scope="total"` では `input_tokens` にキャッシュ読込・書込が含まれます。
`observed` では、それらを入力の通常単価分から引いて各設定単価を掛けます。
単価を別指定したキャッシュ項目が欠測なら、金額は不明です。
`no-cache` はキャッシュ単価を無視し、全入力を通常単価で試算します。請求額の上限ではありません。

Claude Codeの `input_tokens` はキャッシュ分を含まないため、`input_tokens_scope="uncached"` として保持し、
入力・読込・書込を合わせて全入力を求めます。どれか不明なら全入力も金額も推測しません。
この区別は [Claudeのusage仕様](https://platform.claude.com/docs/en/build-with-claude/prompt-caching#tracking-cache-performance)
と [OpenAIのキャッシュusage仕様](https://developers.openai.com/api/docs/guides/prompt-caching) に基づきます。

`cache_write_tokens` は取得できた場合にのみ記録します。Claudeでは `cache_creation_input_tokens`、
Azureでは `prompt_tokens_details.cache_write_tokens`、Codexでは `cache_write_input_tokens` から取得します。
Codexの旧 `cache_write_tokens` も受け付けますが、両方あれば前者を優先します。
推論tokenは出力tokenに含まれるため、出力に加算しません。
0.4以前の記録は新しいコスト情報を持ちません。旧Claude記録を再計算する場合は元データを確認し、
入力の範囲と不足するキャッシュ数を明示してください。
