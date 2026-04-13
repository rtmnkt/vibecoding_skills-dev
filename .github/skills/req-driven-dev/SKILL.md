---
name: req-driven-dev
description: >-
  要件駆動の実装・検証ワークフロー。要件ファイルの読解、コード検証、実装、
  passed_stutas 更新、仕様メモ記録を一貫して行う。
  Use when: 要件を実装する、要件を検証する、passed_stutas を更新する、
  specs を書く、リグレッション確認、"要件0001を対応して",
  "implement requirement", "verify requirements"
argument-hint: '要件ファイル番号 (例: 0001) またはタスクの説明'
---

# Requirements-Driven Development

要件ファイル (.local/requirements/) を起点に、検証→実装→記録を行うワークフロー。

## When to Use

- ユーザーが「要件 XXXX を実装して」「検証して」と言ったとき
- 実装タスクを始めるとき（AGENTS.md ポリシー: 常に要件ファイルから始める）
- `passed_stutas` の更新が必要なとき
- 仕様メモ (.local/specs/) を作成・更新するとき

## Procedure

### Phase 1: 要件の理解

1. `.local/requirements/XXXX.md` を読む
2. フォーマットが不明なら [要件フォーマット](./references/requirements-format.md) を確認
3. `passed_stutas` の既存エントリを確認（ただし無条件に信頼しない）
4. 対応する `.local/specs/XXXX.md` があれば読む → `commits` と `files` の git log を比較し、乖離があればコードと照合

### Phase 2: 検証

各要件を以下の方法で検証:

| 要件タイプ | 検証方法 |
|-----------|---------|
| コードの存在・構造に関するもの | grep / ファイル確認 |
| 動作・出力に関するもの | ビルド + 実行 |
| 既に passed のもの | コード現状と照合して妥当性を再確認 |

- 検証結果は `passed_stutas` に追記（日付 + 要件番号 + status）
- 未対応の要件を特定 → Phase 3 へ

### Phase 3: 実装

1. 計画を立てる（影響範囲・変更ファイルの特定）
2. 実装する
3. ビルド・テストで検証
4. `passed_stutas` を更新
5. 暗黙の要件を発見した場合 → 本文末尾に追記（ユーザーが消すかもしれないが躊躇しない）

### Phase 4: 記録

1. `.local/specs/XXXX.md` を作成または更新
2. フォーマットは [仕様メモフォーマット](./references/specs-format.md) に従う
3. 他の仕様ファイルに影響する変更をした場合、該当ファイルも更新

### リグレッション検出時

過去の `passed_stutas` エントリは消さない。新しい日付で失敗メモを追記:

```yaml
2026-04-10:
  - 30: "REGRESSION: 原因の説明"
```

## Decision Points

- 要件が曖昧なとき → ユーザーに確認。推測で実装しない
- 検証と実装どちらが先か → 全要件を先に検証し、未対応リストを作ってから実装に入る
- specs が古い（commits 乖離）→ コードと照合してから利用。更新も行う
