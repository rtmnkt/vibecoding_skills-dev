# vibecoding_skills-dev

自分専用の開発用スキル集。

## req-driven-dev — 要件駆動開発

要件ファイルと仕様メモによるエージェント協調開発ワークフロー。

- `skills/req-driven-dev/` — スキル定義・リファレンス・CLIツール。

### CLI ツール

```bash
uv run skills/req-driven-dev/scripts/req_tool.py status [req_file]      # 状態確認
uv run skills/req-driven-dev/scripts/req_tool.py criteria add ...       # 受け入れ条件追加
uv run skills/req-driven-dev/scripts/req_tool.py verify ...             # 検証結果記録
uv run skills/req-driven-dev/scripts/req_tool.py validate               # フォーマット検証
```

### WebUI ダッシュボード

```bash
uv run skills/req-driven-dev/scripts/webui.py                          # http://localhost:9421
```

要件の進捗サマリー、検証状態の承認/却下、依存関係の Mermaid 可視化を提供。
