# 変更履歴

## 0.3.0

- README、組み込みガイド、サンプルの記載を整備。

## 0.2.0

- azure-identityを標準依存に含め、認証方法によらず `uv tool install .` でインストールできる構成へ統一。
- Ollama・Azure OpenAIの生成要求をLiteLLM Python SDK経由へ変更。CLI接続は専用アダプターを継続。
- 公開Python API、接続プロファイル、設定スキーマ1.0.0を維持。
- SDKの遅延読み込み、同梱メタデータの利用、再試行・キャッシュ・外部コールバックの抑止。
- ローカルモデルの事前確認、元の応答による完了判定・利用量記録、JSON Schema検証を維持。
- 実SDKと模擬通信による回帰テスト、外部通信を拒否したプロセスでの検証を追加。

## 0.1.0

- Codex、Claude Code、GitHub Copilot、Ollama、Azure OpenAI の共通呼び出しAPI。
- 共有接続プロファイル、設定元の表示、安全な設定作成。
- JSON Schema 検証、オフライン計画、実行情報、標準化した例外。
- 日本語 README と既存CLIの移行ガイド。
