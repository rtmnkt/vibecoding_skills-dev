---
name: req-driven-dev
description: >-
  要件駆動の実装・検証ワークフロー。要件ファイルの読解、受け入れ条件の確定、
  コード検証、実装、状態記録、仕様メモ記録を一貫して行う。
  Use when: 要件を実装する、要件を検証する、状態を確認する、
  specs を書く、リグレッション確認、"要件0001を対応して",
  "implement requirement", "verify requirements"
argument-hint: '要件ファイル番号 (例: 0001) またはタスクの説明'
---

# Requirements-Driven Development

要件ファイルを起点に、条件確定→検証→実装→記録を行うワークフロー。

## データストア

全状態は **JSONL ファイル** (`.local/state/`) に格納。DuckDB でクエリ。

| ファイル | 内容 |
|---------|------|
| `requirements.jsonl` | 要件定義 (`file`, `req_no`, `text`) |
| `specs.jsonl` | 仕様メモ (`requirement`, `commits`, `files`, `title`, `body`) |
| `acceptance_criteria.jsonl` | 受入条件 (`requirement`, `req_no`, `criterion`, `req_text_hash`) |
| `verifications.jsonl` | 検証結果 (`criteria_id`, `status`, `detail`, `limitation`) |
| `approvals.jsonl` | 承認 (`verification_id`, `decision`, `comment`) |

> **Note:** `.local/requirements/*.md` と `.local/specs/*.md` は **レガシー（読取専用）**。
> 新規データは全て JSONL に追記される。CLI / WebUI 共に `state_db.py` 経由でアクセス。

## CLI ツール リファレンス

```bash
# 状態確認（最初に必ず実行）
uv run .github/skills/req-driven-dev/req_tool.py status [req_file]

# 受け入れ条件の管理
uv run .github/skills/req-driven-dev/req_tool.py criteria add <req_file> <req_no> <criterion>
uv run .github/skills/req-driven-dev/req_tool.py criteria list [req_file] [--req-no N]

# 検証結果の記録（受け入れ条件が存在する req_no のみ）
uv run .github/skills/req-driven-dev/req_tool.py verify <criteria_id> <status> [--detail TEXT] [--limitation TEXT]

# リグレッション記録
uv run .github/skills/req-driven-dev/req_tool.py regress <req_file> <req_no> --detail TEXT [--criteria-id ID]

# 承認（ユーザーが実行。エージェントは実行しない）
uv run .github/skills/req-driven-dev/req_tool.py approve <verification_id> <decision> [--comment TEXT]

# 要件の追加
uv run .github/skills/req-driven-dev/req_tool.py req add <file> <text>
uv run .github/skills/req-driven-dev/req_tool.py req list [file]

# 仕様メモの追加
uv run .github/skills/req-driven-dev/req_tool.py spec add <requirement> <title> --body TEXT [--commits ...] [--files ...]

# マイグレーション（.md → JSONL）
uv run .github/skills/req-driven-dev/req_tool.py migrate [req_file]
```

status の値: `passed`, `failed`, `conditional`
decision の値: `approved`, `rejected`

## Web UI ダッシュボード

ブラウザベースのダッシュボード (NiceGUI)。検証状態の俯瞰、承認操作、要件追加が可能。

```bash
uv run .github/skills/req-driven-dev/webui.py [--port PORT]
# デフォルト: http://localhost:9421
```

**Dashboard タブ:**
- サマリーバー (approved / pending / failed / regression / stale)
- フィルター (All / Pending / Problems / Approved)
- 要件カード: 検証詳細の展開、承認・却下ボタン
- 要件・受入条件の追加ダイアログ
- 60秒自動リフレッシュ

**Dependencies タブ:**
- Mermaid ダイアグラムで要件→受入条件→検証→承認の依存関係を可視化
- ノード色: 承認=緑, 保留=黄, 失敗=赤, 古い=オレンジ

## When to Use

- ユーザーが「要件 XXXX を実装して」「検証して」と言ったとき
- 実装タスクを始めるとき（AGENTS.md ポリシー: 常に要件ファイルから始める）
- 検証状態の確認・更新が必要なとき
- 仕様メモを作成・更新するとき

## Procedure

### Phase 1: 要件読解 + 現状把握

1. `.local/requirements/XXXX.md` を読む
2. `uv run .github/skills/req-driven-dev/req_tool.py status XXXX` を実行して現在の検証状態を確認
3. 対応する `.local/specs/XXXX.md` があれば読む → `commits` と `files` の git log を比較し、乖離があればコードと照合
4. フォーマットが不明なら [要件フォーマット](./references/requirements-format.md) を確認

### Phase 2: 意図解釈 + 受け入れ条件確定

要件の意味を解釈し、検証可能な受け入れ条件を確定する。

**条件確定の判断基準:**

| 要件の性質 | アクション |
|-----------|-----------|
| 明確で一意な解釈 | 要件テキストをそのまま条件として `criteria add` で登録 |
| 複数の解釈が可能 | ユーザーに解釈を提案し、対話で確定してから登録 |
| 曖昧・不完全 | ユーザーに明確化を求める。推測で条件を作らない |
| 既に条件が存在 | `criteria list` で確認。stale 警告があれば再確認 |

```bash
# 条件登録例
uv run .github/skills/req-driven-dev/req_tool.py criteria add 0001 10 "BitBlt API で画面全体のスクリーンショットが取得できること"
```

**暗黙の要件を発見した場合:**
本文に追記するのではなく、ユーザーに「この暗黙の要件も追加すべきでは？」と提案する。
ユーザーが同意すれば、ユーザー自身が要件ファイルに追記する。

### Phase 3: 影響度分析 + 計画

1. 未対応の要件・条件を特定
2. コードベースの影響範囲を調査
3. 変更ファイル・手順の計画を立てる
4. 非自明なタスクでは rubber-duck サブエージェントで計画をレビュー

### Phase 4: 実装

1. コード変更を実施
2. ビルド・テストで動作確認
3. 各段階で検証可能な状態を維持

### Phase 5: 検証

各受け入れ条件に対して検証を実施し、結果を記録する。

| 要件タイプ | 検証方法 |
|-----------|---------|
| コードの存在・構造 | grep / ファイル確認 |
| 動作・出力 | ビルド + 実行 |
| 既に verified のもの | コード現状と照合して妥当性を再確認 |

```bash
# 検証結果の記録
uv run .github/skills/req-driven-dev/req_tool.py verify ac-1 passed --detail "cargo test で全テスト通過"
uv run .github/skills/req-driven-dev/req_tool.py verify ac-3 conditional --detail "背後ウィンドウOK" --limitation "最小化ウィンドウは不可"
uv run .github/skills/req-driven-dev/req_tool.py verify ac-5 failed --detail "コンパイルエラー: xxx"
```

**注意:** `approve` コマンドはエージェントが実行するものではない。ユーザーが検証結果を確認して承認する。

### Phase 6: 報告 + 記録

1. `uv run .github/skills/req-driven-dev/req_tool.py spec add` で仕様メモを JSONL に追記
2. 他の仕様に影響する変更をした場合、該当仕様も更新
3. `uv run .github/skills/req-driven-dev/req_tool.py status XXXX` で最終状態を表示

### リグレッション検出時

既存の検証結果は残したまま、新しいリグレッション記録を追加する:

```bash
uv run .github/skills/req-driven-dev/req_tool.py regress 0003 10 --detail "Aggregator状態マシン欠陥で連続変化中にイベント不発行"
```

## Decision Points

- 要件が曖昧なとき → ユーザーに確認。推測で実装しない
- 検証と実装どちらが先か → 全要件を先に検証し、未対応リストを作ってから実装に入る
- specs が古い（commits 乖離）→ コードと照合してから利用。更新も行う
- stale 警告が出たとき → 要件テキストが変更されている。条件を再確認

## 禁止事項

- 要件ファイルの本文を勝手に編集しない（ユーザー所有）
- `approve` コマンドを実行しない（ユーザー操作）
- CLI ツールを経由せずに検証状態を変更しない
