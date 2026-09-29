# 画像入力とVLMの対応・比較

Tkn GenAI Bridgeは、テキストと画像を接続先へ渡し、返ってきた説明をJSON Schemaで検証します。画像内の物体・文字・配置をまとめて説明する用途では、画像を扱えるモデル（VLM）を選びます。画像から読めない情報を補完したり、OCRの転記精度や説明内容の正しさをBridgeが保証したりする機能ではありません。

## 対応の確認は3段階

1. **Bridgeのアダプター**：以下の6種類は画像を渡す処理を実装しています。入力はPNG・JPEG・WebPで、1枚20 MiB以下です。
2. **CLI・APIの経路**：接続先のCLIが画像オプションを備え、またはAPI経路とLiteLLMが画像を転送できる必要があります。Bridgeの実装と自動テストで確認した範囲を下表に示します。
3. **選択したモデル**：同じサービス内でも画像対応、枚数・解像度の上限、利用可能なモデルは異なります。`model: null` のCLIプロファイルはCLIの既定モデルを使うため、特定のVLMを保証しません。モデルとアカウントの利用可否は実行環境で確認してください。

「GPT-5以上はすべて対応」のように、モデル名の系列だけで判定しません。GitHubも[画像入力にはモデルのvision対応が必要](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/overview)と説明しています。API経路では[LiteLLMの画像入力形式](https://docs.litellm.ai/docs/completion/vision)と、接続先モデルの仕様の両方を確認します。Bridgeは全モデルの対応一覧を内蔵せず、`plan()` もモデルへ通信して確認しません。

## 接続先の比較

| 接続先・プロファイル例 | Bridgeから画像を渡す方法 | このリポジトリで確認した範囲 | モデル・利用量の記録 |
| --- | --- | --- | --- |
| Codex・`codex-default` | 専用一時フォルダの画像をCLIの`--image`へ渡す | JPEG写真とPNG画面で実生成を確認 | CLIが報告した値のみ。モデルIDは未取得の場合がある |
| Claude Code・`claude-default` | 標準入力のbase64画像ブロック | 同じ2枚でBridge経由の実生成を確認 | 最終応答のモデルと利用量を取得できる場合がある |
| GitHub Copilot・`copilot-default` | 専用一時フォルダの画像をCLIの`--attachment`へ渡す | 同じ2枚でBridge 0.10.0経由の実生成・JSON検証を確認 | 画像ありでは最終メッセージのモデルIDを取得。token利用量は未取得 |
| Google Antigravity・`antigravity-default` | CLIに絶対パスを指示し、`view_file`で開く | 同じ2枚でBridge経由の実生成と全画像の読取完了を確認 | 最終resultの利用量を取得。実モデルIDは未取得 |
| Ollama・`local-vision` | LiteLLM経由でローカルサーバーの`messages[].images`へ渡す | 実SDKと模擬HTTP応答で画像バイト列・順序を確認。`qwen3.5:9b`によるJPEG写真・PNG画面の実生成も確認 | 接続先が報告した値のみ |
| Azure OpenAI・画像対応デプロイ | LiteLLM経由で画像の`image_url`をAPIへ渡す | 実SDKと模擬HTTP応答で画像バイト列・順序を確認。画像付きの実モデル生成は未確認 | 接続先が報告した値のみ |

実生成の確認は2026年9月27日のローカル環境で行いました。各CLIの既定モデルやアカウント設定は変わり得ます。Copilotで観測した応答モデルは`claude-sonnet-5`、Claude Codeでは`claude-opus-5-5`でした。これらは当日の実行結果であり、他のモデルやアカウントの対応を示す一覧ではありません。接続確認だけから認識精度の順位は付けられません。

BridgeがLiteLLMを使うのはOllamaとAzure OpenAIです。Codex・Claude Code・Copilot・Antigravityは各CLIを直接呼び出すため、これらの画像入力についてLiteLLMの対応状況は判定要素ではありません。転送の詳細は[接続仕様](providers.md#画像の転送)を参照してください。

## モデルを選ぶ目安

次は2026年9月27日の探索的な実画像評価から得た**初期候補**です。料理写真とExcel画面の2枚を、それぞれ同じ課題文・JSON SchemaでGPT-6 Astra、GPT-6 Sol、ローカルOllamaの`qwen3.5:9b`へ1回ずつ送り、出力を元画像と照合しました。Codex CLIではAstraとSolを明示して要求しましたが、応答にモデルIDがないため実際の返却モデル名では二重確認できていません。画像の正解メモはモデルへ送りませんでした。

| 選択で重視すること | 初期候補 | この2枚で分かったこと・確認事項 |
| --- | --- | --- |
| 画像を外部へ送れない、または人が見直す前提で大量に分類する | `local-vision`（Qwen3.5:9b） | 主な被写体や画面の種類は捉えた。一方、写真の大根おろしを白飯／お茶漬け、パセリを小松菜と誤認した。画面の細い記号や日本語も誤読した。`uncertain`が空でも正しいとは限らない。 |
| クラウドで一般的な画像説明や画面読取を始める | GPT-6 Sol | 料理と画面の主要構造を概ね説明し、見えない中身は保留した。小さい記号の転記には誤りもあった。厳密な抽出は元画像で確認する。 |
| 複雑な配置や周辺物を詳しく拾う | GPT-6 Astra | 写真の配置・周辺物、画面の構成をより細かく記述した。ただし小さい日本語の注記ではSolのほうが近い箇所もあり、細字OCRで常に優位とは言えない。 |
| 別のCLIや契約を使いたい | Claude Code、Copilot、Antigravity | 画像を渡して応答を得ることは確認済み。上の3モデルとは課題文・画像の渡し方が異なるため、この評価から精度や速度の順位は付けない。 |

今回の結果では、Solをクラウド側の比較基準にし、配置や見落としが重要な場合にAstraを追加で試すのが妥当です。ローカルQwenの説明は検索用タグや下書きに使えますが、食材名・数値・画面上の文字を無確認で確定情報にしないでください。Claude Codeなども、利用可能なモデルと自分の画像で同じ項目を試して選びます。Azure OpenAIの画像付き実生成はまだ確認していません。

Codexでモデルを固定して比べるときは、補助CLIに`--profile codex-default --model gpt-6-sol`または`--model gpt-6-astra`を指定します。Python APIなら`load_profile("codex-default", overrides={"model": "gpt-6-sol"})`を使用します。モデルを省略した場合はCodex CLIの既定値になり、後日同じモデルで再現できるとは限りません。

費用と速度も判断材料です。評価時の2画像・各1回では、Astraが約24秒と38秒、Solが約46秒と26秒、Qwenが約46秒と12秒でした。Qwenの最初の実行はモデルのロード時間を含む可能性があります。これらはBridgeの検証完了までの時間で、純粋なモデル速度や安定した平均値ではありません。[OpenAIのAPI価格表](https://developers.openai.com/api/docs/pricing)では同じ処理区分のAstraの入力・出力単価はSolの5倍ですが、Codex CLIの請求額やクレジット消費をこのAPI単価から確定することはできません。ローカルOllamaには外部のtoken従量課金がなくても、計算機・電力・待ち時間が必要です。

この比較は2種類の画像・各1回に限られ、一般的な正解率を示しません。Claude CodeとAntigravityの以前の実行は2枚同時・別の課題文であり、Copilotの動作確認も別条件です。画像の縮小・切り出し別の比較もしていません。重要な文字や記号は原寸または必要箇所を切り出して再確認し、最終的には人が元画像と照合してください。Bridgeは自動で画像を加工しません。

## 同じ入力で比較する

接続先ごとにプロファイルだけを切り替え、同じプロンプト・スキーマ・画像の順序で実行します。例えば、写真なら主な被写体と周囲の物、画面なら見出し・文字・左右の関係、さらに判別できない点を尋ねます。`result.data` の説明を元画像と照合してください。JSON Schema検証の成功は、説明の正しさを意味しません。写真だけでは分からない料理名・店名、蓋で見えない中身などを断定していないかも確認できます。

比較記録には、要求したプロファイルとモデル、取得できた`record.response_model`、`record.input_sha256`、実行時間、利用量の完全性を残します。`input_sha256` はプロンプトと画像の内容・順序に依存するため、同じ入力かどうかの確認に使えます。CLI内部の複数回のモデル呼び出し、画像の前処理、キャッシュ、料金体系は接続先で異なり、1回の実行時間や報告token数だけからモデルの優劣や費用を比較できません。

## 使い方

Python APIでは画像を読み込み、`GenerationRequest.images`へ指定順に渡します。プロファイル名の変更だけで上表の接続先を切り替えられます。

```python
from tkn_genai_bridge import GenerationRequest, ImageInput, Runtime, load_profile

request = GenerationRequest(
    prompt="画像1と画像2の配置と読める文字を説明し、不明な点も示してください。",
    output_schema={
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
        "additionalProperties": False,
    },
    images=[ImageInput.from_file("overview.png"), ImageInput.from_file("detail.png")],
)
with Runtime(load_profile("codex-default")) as runtime:
    plan = runtime.plan(request)  # 通信・画像の一時保存なし
    result = runtime.generate(request)
print(result.data["summary"])
```

補助CLIでは`--image`を繰り返します。`--dry-run`を外すと選択した接続先へ画像を送信します。

```shell
tkn-genai-bridge generate --no-project-config --profile codex-default --prompt-file examples/vision-prompt.txt --schema-file examples/vision-output.schema.json --image overview.png --image detail.png --dry-run
```

ローカル限定の画像説明には組み込みの`local-vision`を選べます。既定モデルは`qwen3.5:9b`で、Ollamaの起動とモデル取得は利用者が行います。未取得なら`ollama pull qwen3.5:9b`を実行し、`--profile local-vision`を指定します。`local_only: true`に加えて、[Ollama側のクラウド無効化](providers.md#ollama)も設定してください。初期値は`think: false`、出力上限2,048 token、コンテキスト16,384 tokenです。詳細は[接続設定](configuration.md#ollama)を参照してください。

画像は読み込み時にメモリへ固定し、元ファイルは変更しません。Codex・Copilot・Antigravityでは実行時に連番の一時画像を作成し、終了時に削除します。URL取得、画像変換・自動縮小、画像生成は行いません。画像付きの事前入力token概算は既定で`null`です。記録には画像本体や元のファイル名を含めず、ハッシュ・形式・サイズを残します。契約の詳細は[Python API](api.md#画像入力)、tokenと費用は[コスト概算](costs.md#生成前の確認)を参照してください。
