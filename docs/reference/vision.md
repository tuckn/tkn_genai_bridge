# VLM比較結果：料理写真とExcel画面

**GPT-6.1 SolとGemini 4 Argonを、`examples` の2画像で実測しました。** 両方とも料理・画面の主要内容を認識できました。細字の転記は今回Argonが良好でしたが、時計の誤読や説明の補完もありました。ローカルQwenは大分類には使えますが、食材や細字を確認する用途では誤りが目立ちました。

更新日：**2026-10-31**。Sol 6.1／Argonは今回の新規実測、Astra／Qwen／Opusは2026-09-27の保存済み実測です。**旧GPT-6 Solは比較対象・実行例から外しました。旧結果をSol 6.1の結果として流用していません。**

| 確認したいこと | 読む場所 |
| --- | --- |
| 各モデルの結果 | [比較の一覧](#results) |
| 具体的な誤認・誤読 | [料理写真](#photo-results)・[Excel画面](#screen-results) |
| 速さ・費用 | [所要時間](#timing)・[費用表](costs.md#vision-costs) |
| 条件・証拠 | [比較条件](#conditions)・[保存記録](#evidence) |
| Bridgeへの画像の渡し方 | [接続先の比較](#接続先の比較)・[使い方](#使い方) |

<a id="results"></a>

## 比較の一覧

| モデル | 料理写真 | Excel画面 | 評価日・条件 |
| --- | --- | --- | --- |
| **GPT-6 Astra** | 配置・周辺物まで詳しく説明。食材名の一部は保留 | UI・配列を認識。小さい「ぬ」を「む」と誤読 | 9/27・1枚ずつ |
| **GPT-6.1 Sol** | 配置・器・店名を詳しく説明。大根おろしは候補として提示 | UI・主要注記を認識。「ぬ」を「む」と誤読 | **10/31・1枚ずつ** |
| **Gemini 4 Argon（Medium）** | 配置・店名を認識。パセリ・からし・大根おろしを候補として提示 | **「]}ぬ」と `_` を取得**。時計は誤読、説明が指定字数を超過 | **10/31・1枚ずつ** |
| **Qwen3.5:9b（Ollama）** | とんかつ定食と認識。**大根おろし→白飯／お茶漬け、パセリ→小松菜** | 配列の大意は取得。括弧の順序・日本語を誤読 | 9/27・1枚ずつ |
| **Claude Opus 5.5** | とんかつ・大根おろし・キャベツを認識 | JIS/ANSIの左右、ローマ字/かなの上下を認識 | 9/27・**別条件：2枚同時の短い説明** |

同一課題の4モデルも実行日・CLI版が異なります。Opusは注記の全文転記を求めていないため、同じOCR評価として順位付けしません。この2枚・少数回の結果からモデル全般の正解率や総合順位は決められません。

モデルIDは `gpt-6-astra`、`gpt-6.1-sol`、`gemini-4-argon-medium`、`qwen3.5:9b`、`claude-opus-5-5`。Codex／Antigravityは**要求したID**で表記しています。応答モデルIDは取得できず、返却値での二重確認はできません。Claude／Qwenは返却IDも確認しています。

### 比較に使った画像

| 入力 | ファイル | 寸法 | 課題 |
| --- | --- | --- | --- |
| 料理写真 | [photo 特ロースかつ定食.jpg](../../examples/photo%20特ロースかつ定食.jpg) | 1280 × 964 px | 主菜、付け合わせ、器、周辺物、店名 |
| Excel画面 | [screenshot_Excel-note.png](../../examples/screenshot_Excel-note.png) | 1927 × 1437 px | UI、図の上下左右、細い記号、日本語注記 |

画像は加工せず使用。今回の4件は、画像・課題文・JSON Schemaを含む `input_sha256` が9/27の同一課題と一致しました。ユーザー提供の正解メモは照合時だけ参照し、モデルには渡していません。

<a id="photo-results"></a>

## 料理写真：何を認識できたか

「保留」は断定しなかったこと、「誤認」は別の物として答えたことです。名前を保留した整合的な描写と、明確な誤認を区別します。

| 確認項目 | Astra | Sol 6.1 | Argon Medium | Qwen（ローカル） |
| --- | --- | --- | --- | --- |
| 主菜・網・切り分け | カツと網。肉種は保留 | カツと網。肉種は保留 | カツと網。肉種は保留 | とんかつと認識 |
| ご飯・キャベツ | ご飯、細切り野菜 | ご飯、千切りキャベツ | ご飯、キャベツ・紫キャベツ | 認識。紫色部分は紫玉ねぎと断定 |
| 大根おろし | 白いおろし状＋青ねぎ | 大根おろしと思われるもの＋ねぎ | 大根おろしのようなもの＋ねぎ | **白飯／お茶漬けと誤認** |
| パセリ・からし | 緑の葉、黄色いペースト。種類は保留 | 緑の葉、からし状のもの。種類は保留 | パセリ・からしのようなもの | **パセリを小松菜と誤認** |
| 箸袋の店名 | 主要文字と位置を認識 | `TONKATSU WAKO`・`和幸`と位置を認識 | 同左 | 余分な字を挿入し、位置をトレイの端と誤説明 |
| 蓋のある容器 | 中身は保留。匙も認識 | 中身は保留。スプーンも認識 | 中身は保留。さじも認識 | 椀の中身は保留。小容器の説明は不足 |
| 周辺物・細部 | グラス・白い器・布／紙など | グラス・白い器・布／紙など | 周辺物を説明。ただし白い物を紙ナプキンとし、読みにくい案内文も補完 | ティーポットと断定するなど補完も混在 |

**今回のSol 6.1／Argonは、大根おろしを別の料理とする誤認はありませんでした。** Argonの案内プレートの転記は、画像から全文を裏付けられないため正解扱いにしていません。支店・日時・価格・肉の部位・蓋の中身など、画像から確認できない背景情報も必須正解には含めません。

**Opus 5.5（別条件）：** とんかつ、ご飯、ソース、大根おろし、キャベツ、箸袋の店名を認識。緑の付け合わせの種類、蓋や瓶の中身は保留しました。

<a id="screen-results"></a>

## Excel画面：何を読み違えたか

| 確認項目 | Astra | Sol 6.1 | Argon Medium | Qwen（ローカル） |
| --- | --- | --- | --- | --- |
| Excel・選択シート・ファイル名 | 認識 | 認識 | 認識。Claudeボタンも説明 | 認識 |
| 左JIS/JP・右ANSI/US | 正しい | 正しい | 正しい | 正しい |
| 上段と中段 | ローマ字／かなの対応 | 英字・記号／かなの対応 | ローマ字／かなの対応 | 切り替え状態という曖昧な説明 |
| 右上ピンク注記の `]}ぬ` | 「む」と誤読。不確実性を申告 | **「む」と誤読**。不確実性を申告 | **「]}ぬ」を転記**。原文は誤記ではないかという推測を別記 | **括弧の順序を逆転** |
| 中央右のsc07D注記 | キー不在・Shift+-・長音を取得 | `_`・Shift+-・長音を取得 | `_`・Shift+-・長音を取得 | 「存在しない」→「存在ない」、「でる」→「得る」 |
| 下の赤字注記 | 概ね一致 | 概ね一致 | 概ね一致 | **「でる」→「得る」** |
| AutoSave On／Saved | 両方明示 | 両方明示 | 両方明示 | 省略 |
| 色分けの意味 | 打鍵コスト／五十音行の説明なし | 同左 | かな図を「運指に応じた色分け」と説明。正解メモの五十音行と不一致 | 一般的な「機能による色分け」と説明 |
| その他の誤読 | 小さい字形を保留 | 小さい字形を保留 | **時計を約9:30と誤読**（正解メモは約7:52） | **誤記があっても `uncertain: []`** |

**指定した3注記では、今回のArgonは問題の「ぬ」と細い `_` を読み取れました。** 一方、画面全体の説明には誤りがあり、「Argonならすべて正確」とは言えません。文字単位の正解率は算出していません。

Argonの画面回答は `answer` と `evidence` の合計**2,542文字**で、指定の1,600字を超えました（Markdown記号・改行を含む文字数）。Sol 6.1は1,101文字です。詳しさだけでなく、長さの指示に従ったかも評価上の差です。JSON検証の成功は字数や認識内容の正しさを保証しません。

**Opus 5.5（別条件）：** 配列の見出し、左右・上下の関係、選択シート、sc07Dなどを認識。3注記の全文転記による比較は未実施です。

<a id="timing"></a>

## 所要時間

各画像について、Bridge呼び出し開始から検証完了までの実測です。

| モデル | 料理写真 | Excel画面 | 実行日・採用回数 |
| --- | ---: | ---: | --- |
| GPT-6 Astra | 24.22秒 | 38.26秒 | 9/27・各1回 |
| GPT-6.1 Sol | **17.84秒** | **29.81秒** | 10/31・各1回 |
| Gemini 4 Argon（Medium） | **34.89秒** | **35.67秒** | 10/31・修正後各1回 |
| Qwen3.5:9b | 45.83秒 | 11.57秒 | 9/27・各1回 |

平均速度ではありません。日付・CLI版・出力長が異なり、一部の実行は別モデルと並行しました。Qwenの写真は初回ロードを含む可能性があり、画面はその直後の実行です。Opus 5.5は別課題で**2枚まとめて10.49秒**でした。

Argonでは、修正前の2試行と原因調査の1試行も実行しました。これらは画像読取イベントをBridgeが受理できず失敗したため、認識表には採用せず、時間・利用量を[失敗・診断分](costs.md#argon-attempts)に残しています。

<a id="conditions"></a>

## 比較条件と再実行

| 項目 | 条件 |
| --- | --- |
| 入力 | 上記2画像を1枚ずつ、同じ課題文・JSON Schemaで送信 |
| 写真の課題 | 主菜・各皿・周辺物・店名を説明。見えない内容を推測せず、説明は合計1,200字以内 |
| 画面の課題 | UI・図・見出しを説明。右上ピンク／中央右sc07D／下の赤字の3注記を転記。合計1,600字以内 |
| 応答形式 | `answer`、`evidence`、`uncertain` |
| Astra（9/27） | Codex CLI 0.158.0-alpha.2.1、medium、Bridge 0.9.1 |
| Sol 6.1（10/31） | Codex CLI 0.164.0、medium、Bridge 0.11.0 |
| Argon（10/31） | Antigravity CLI 1.4.12、`gemini-4-argon-medium`、effort medium、Bridge 0.11.0＋本変更 |
| Qwen（9/27） | Ollama 0.34.3、Q4_K_M、`think: false`、出力2,048 token、context 16,384 token、Bridge 0.9.1 |
| ローカル実機 | RTX 4070 Ti・VRAM約12 GiB、Core i9-13900K、RAM 64 GiB |
| 未測定 | 反復の安定性、縮小・切り出しによる差、最大画像サイズ、実クレジット消費 |

サービス内部の指示・リサイズ・トークナイザーまでは統一できていません。Argonは `agy models` に掲載されたモデルIDを明示しました。CLI 1.4.12の画像読取イベント `read_file` / `Path` にBridgeを対応させ、全画像の完了確認を通過した結果を採用しています。

モデル指定例：

```shell
# generate の課題文・schema・image指定に加えるオプション
--profile codex-default --model gpt-6.1-sol --reasoning-effort medium
--profile codex-default --model gpt-6-astra --reasoning-effort medium
--profile antigravity-default --model gemini-4-argon-medium --reasoning-effort medium
--profile local-vision
```

モデル省略はCLIの既定値です。`examples/vision-prompt.txt` は汎用の説明用で、今回の比較課題とは異なります。再比較時は保存した課題文・Schemaを使い、画像・入力ハッシュとCLI版を記録してください。正解メモはモデルに渡さず、失敗や再試行の利用量も残します。

<a id="evidence"></a>

## 保存記録との対応

全応答・正解メモはローカルに保持し、Git管理の文書には比較に必要な要約と測定値を残します。

| 保存先・記録の識別子 | 内容 |
| --- | --- |
| `.local/state/20261031T131859_vision-model-refresh/sol61-photo.json`・`sol61-screen.json` | Sol 6.1の新規応答・時間・token・入力ハッシュ |
| 同フォルダの `verified-argon-photo.json`・`verified-argon-screen.json` | 互換修正後のArgonの新規応答・時間・token・入力ハッシュ |
| 同フォルダの `argon-*.json`・`diagnostic-argon-photo*` | 修正前2試行・原因調査1試行。比較の成功件数に含めない |
| `.local/data/20261031T131859_vision-model-refresh/requests.json` | 今回使用した課題文・Schema・画像ハッシュ。過去の正解・回答は含まない |
| `.local/vision-smoke-20260927/evaluation-v2/astra-*.json`・`qwen-*.json` | Astra／Qwenの過去の同一課題結果 |
| `.local/vision-smoke-20260927/bridge-0.9.0-claude-default.json` | Opus 5.5の別条件の結果 |

旧GPT-6 Solの記録は `evaluation-v2/sol-*.json` に履歴として保持しています。現在の比較表・費用表・実行対象には含めません。

---

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
| Google Antigravity・`antigravity-default` | CLIに絶対パスを指示し、読取完了イベントを確認 | 同じ2枚でBridge経由の実生成と全画像の読取完了を確認 | 最終resultの利用量を取得。実モデルIDは未取得 |
| Ollama・`local-vision` | LiteLLM経由でローカルサーバーの`messages[].images`へ渡す | 実SDKと模擬HTTP応答で画像バイト列・順序を確認。`qwen3.5:9b`によるJPEG写真・PNG画面の実生成も確認 | 接続先が報告した値のみ |
| Azure OpenAI・画像対応デプロイ | LiteLLM経由で画像の`image_url`をAPIへ渡す | 実SDKと模擬HTTP応答で画像バイト列・順序を確認。画像付きの実モデル生成は未確認 | 接続先が報告した値のみ |

接続確認は2026年9月27日、Sol 6.1／Argonの追加比較は2026年10月31日のローカル環境で行いました。各CLIの既定モデルやアカウント設定は変わり得ます。Copilotで観測した応答モデルは`claude-sonnet-5`、Claude Codeでは`claude-opus-5-5`でした。これらは当日の実行結果であり、他のモデルやアカウントの対応を示す一覧ではありません。接続確認だけから認識精度の順位は付けられません。

BridgeがLiteLLMを使うのはOllamaとAzure OpenAIです。Codex・Claude Code・Copilot・Antigravityは各CLIを直接呼び出すため、これらの画像入力についてLiteLLMの対応状況は判定要素ではありません。転送の詳細は[接続仕様](providers.md#画像の転送)を参照してください。

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
