# vibecoding_skills-dev

自分専用の開発用スキル集。

## req-driven-dev — 要件駆動開発

要件ファイルと仕様メモによるエージェント協調開発ワークフロー。

- `.local/requirements/` — 要件定義（WHAT）。ユーザーが記述。
- `.local/specs/` — 仕様メモ（HOW/WHY）。エージェントが記述。
- `.local/state/` — 受け入れ条件・検証・承認の JSONL 状態ファイル。
- `.github/skills/req-driven-dev/` — スキル定義・リファレンス・CLIツール。

### CLI ツール

```bash
uv run .github/skills/req-driven-dev/req_tool.py status [req_file]     # 状態確認
uv run .github/skills/req-driven-dev/req_tool.py criteria add ...       # 受け入れ条件追加
uv run .github/skills/req-driven-dev/req_tool.py verify ...             # 検証結果記録
uv run .github/skills/req-driven-dev/validate.py                        # フォーマット検証
```

### WebUI ダッシュボード

```bash
uv run .github/skills/req-driven-dev/webui.py                          # http://localhost:9421
```

要件の進捗サマリー、検証状態の承認/却下、依存関係の Mermaid 可視化を提供。