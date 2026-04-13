# vibecoding_skills-dev

自分専用の開発用スキル集。

## req-driven-dev — 要件駆動開発

要件ファイルと仕様メモによるエージェント協調開発ワークフロー。

- `.local/requirements/` — 要件定義（WHAT）。ユーザーが記述。
- `.local/specs/` — 仕様メモ（HOW/WHY）。エージェントが記述。
- `.github/skills/req-driven-dev/` — スキル定義・リファレンス。
- `.local/validate.py` — フロントマター検証スクリプト (`uv run .local/validate.py`)。