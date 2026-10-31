# token・コスト概算

**2画像の認識結果に対応する費用表です。** 実測したtoken数をAPI単価に当てはめた参考額と、実請求・クレジット消費は区別します。設定方法は[後半](#単価の設定)にあります。

<a id="vision-costs"></a>

## 2画像の費用比較

更新日：**2026-10-31**。[認識結果・実行条件](vision.md#results)と併せて確認してください。旧GPT-6 Solは対象から外し、GPT-6.1 Solの新規実測に置き換えました。

| モデル | 写真のAPI単価換算 | 画面のAPI単価換算 | 2枚合計 | tokenの測定日 |
| --- | ---: | ---: | ---: | --- |
| GPT-6 Astra | $0.241840 | $0.280380 | **$0.522220** | 9/27 |
| **GPT-6.1 Sol** | **$0.054822** | **$0.062874** | **$0.117696** | **10/31** |
| **Gemini 4 Argon（Medium）** | 換算保留 | 換算保留 | **実クレジット消費も未測定** | **10/31** |
| Qwen3.5:9b（ローカル） | 外部token課金なし | 外部token課金なし | 電力・機器費は未測定 | 9/27 |
| Claude Opus 5.5 | 個別には未測定 | 個別には未測定 | この表では未算出 | 9/27・2枚同時の別課題 |

**Codexの金額はAPI Standard単価への換算で、ChatGPT／Codexの支払額やクレジット消費を示しません。** 今回の2枚ではSol 6.1の参考額がAstraより小さくなりましたが、実行日・CLI版が異なり、すべての画像で同じ比率になるとは限りません。

### 換算に使った数値

| モデル | 写真：入力 / 出力token | 画面：入力 / 出力token | 入力 / 出力単価（USD・100万tokenあたり） |
| --- | ---: | ---: | ---: |
| GPT-6 Astra | 20,849 / 667 | 22,313 / 1,145 | $10 / $50 |
| GPT-6.1 Sol | 24,026 / 677 | 25,492 / 1,189 | $2 / $10 |
| Qwen3.5:9b | 1,415 / 501 | 2,931 / 689 | 適用なし |

式：`(入力token × 入力単価 + 出力token × 出力単価) / 1,000,000`。上記Astra／Sol 6.1の4件はキャッシュ読取・書込とも報告値0。推論tokenは出力の内数なので再加算していません。API単価は[OpenAI価格表](https://developers.openai.com/api/docs/pricing)を2026-10-31に確認したStandard・短いコンテキストの値です。Astraの値は9/27の保存済み換算条件とも一致します。

### Argonのtokenと換算を保留する理由

| 成功した試行 | CLIの入力token報告 | 出力token報告 | キャッシュ読取token報告 | 推論token報告 |
| --- | ---: | ---: | ---: | ---: |
| 写真 | 16,832 | 1,546 | 11,423 | 763 |
| Excel画面 | 16,817 | 1,788 | 11,437 | 101 |

**Antigravityの入力tokenにキャッシュ分が含まれるかを、今回の記録だけでは確定できません。** 診断時の個別イベントには入力3,351に対してキャッシュ読取11,414という報告がありました。現行Bridgeは `input_tokens_scope: total` を付けますが、このラベルだけを根拠にキャッシュ分を差し引いて料金計算すると誤る可能性があるため、ここでは金額を算出していません。元の報告値は保持しています。

参考として、[Gemini API価格表](https://ai.google.dev/gemini-api/docs/pricing)のGemini 4 Argonは、確認日時点のStandard単価が入力$2・出力$10・キャッシュ読取$0.10／100万tokenです。この価格は2026-11-30まで、12/1以降はそれぞれ$4・$20・$0.20と案内されています。**Gemini APIの価格をAntigravityのクレジット消費に読み替えることはできません。**

<a id="argon-attempts"></a>

### 失敗・診断分も含めた実行履歴

ArgonはCLI 1.4.12の画像読取イベント変更により、修正前はBridgeが `image_read_failed` で停止しました。応答後の検証失敗なので、利用量を0にはしません。

| 試行 | 結果 | 秒 | 入力 / 出力token報告 | キャッシュ読取token報告 |
| --- | --- | ---: | ---: | ---: |
| 写真・初回 | 画像読取確認失敗 | 32.490 | 16,818 / 1,363 | 11,408 |
| 画面・初回 | 画像読取確認失敗 | 37.436 | 16,800 / 1,520 | 11,429 |
| 写真・診断 | 画像読取確認失敗。イベント形式を確認 | 25.893 | 16,819 / 1,016 | 11,414 |
| 写真・修正後 | 成功・比較に採用 | 34.894 | 16,832 / 1,546 | 11,423 |
| 画面・修正後 | 成功・比較に採用 | 35.665 | 16,817 / 1,788 | 11,437 |

この更新ではSol 6.1が2回、Argonが5回の計7回を実行しました。Astra／Qwen／Opusの再実行はしていません。比較表の成功2件と、作業全体の消費量を混同しないでください。

CLIのtokenは画像だけでなく、指示やツール処理も含みます。画像だけのtoken数、請求額、残クレジット、ローカルPCの電力量は測っていません。取得済みの全記録の所在は[保存記録](vision.md#evidence)を参照してください。

## Bridgeの料金計算を使うには

| やりたいこと | 設定・確認する場所 |
| --- | --- |
| モデルの参考単価を登録する | [単価の設定](#単価の設定) |
| 生成前に仮の金額を計算する | [生成前の確認](#生成前の確認)。画像の入力tokenは既定で不明 |
| 実行後のtokenで換算する | [生成後と再計算](#生成後と再計算) |
| 金額が `null` になる理由を調べる | [生成後と再計算](#生成後と再計算)の算出不能理由 |
| キャッシュや途中失敗を扱う | [総量・既知小計・完全性](#総量既知小計完全性)・[キャッシュと推論token](#キャッシュと推論token) |

許容額、token・回数の上限、停止・続行、複数呼び出しの予約額や予算は利用側CLIが管理します。
サブスクリプション、ツール料金、税、為替、段階制・長文料金、異なるモデルの混在やキャッシュ保持時間の違いは自動判定しません。
単価取得や更新の通信は行わず、LiteLLMの価格表も計算には使用しません。

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

画像付きリクエストでは、モデルによる画像処理をUTF-8バイト数へ換算できないため、既定の
`token_estimate.input_tokens` は `null`、方法は `image-input-unestimated-v1` です。
単価設定済みでも事前参考額は `usage_missing` となります。
`input_tokens` / `--estimate-input-tokens` を指定する場合は、画像・テキスト・余裕分を含む総推定値を渡してください。
実行後の参考額は画像なしの場合と同じく、接続先が報告した利用量を使います。

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

### 利用側で予算と上限を確認する

生成の直前に、計画のtoken数・参考額・通貨を、利用側の上限・既使用量・予約量と照合してください。
金額が算出不能の場合に停止するか、分割・修復・再試行にどれだけ予算を予約するかも利用側で決めます。
失敗・通信断時は課金の有無が分からない場合があるため、取得済みの利用量と送信状態を確認し、予約量を解放するかを判断してください。

APIへ送る出力上限には `Profile.max_output_tokens` を使います。
`plan(..., output_tokens=...)` は見積もりの仮定値であり、この上限を変更しません。
単価は概算結果の `pricing` に保存されるため、利用側の記録にも保持すると単価改定前後の計算を確認できます。
単価や予算上限の変更だけを、生成内容の再生成条件に含める必要はありません。

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
