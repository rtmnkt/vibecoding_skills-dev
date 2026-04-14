# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml", "duckdb"]
# ///
"""
Requirements state management CLI.

This tool is the sole interface for managing requirement verification state.
State is stored as JSONL files (sorted rewrite on each mutation).

Usage:
    uv run .github/skills/req-driven-dev/req_tool.py <command> [args...]

Commands:
    criteria add <req_file> <req_no> <criterion>
    criteria list [req_file] [--req-no N]
    verify <criteria_id> <status> [--detail TEXT] [--limitation TEXT] [--by NAME]
    approve <verification_id> <decision> [--comment TEXT]
    regress <req_file> <req_no> --detail TEXT [--criteria-id ID]
    status [req_file]
    migrate [req_file]
    req add <file> <text>
    req list [file]
    spec add <requirement> <title> --body TEXT [--commits ...] [--files ...]
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
from pathlib import Path

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))

import state_db


def extract_front_matter(text: str) -> dict | None:
    """Parse YAML front matter from legacy .md files (migration-only)."""
    import yaml

    match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return None
    try:
        data = yaml.safe_load(match.group(1))
        return data if data else {}
    except yaml.YAMLError:
        return None


def _resolve_req_file_md(name: str) -> Path:
    """Resolve legacy requirement .md file by number (migration-only)."""
    if not name.endswith(".md"):
        name = f"{name}.md"
    p = state_db.get_paths().requirements_dir / name
    if not p.exists():
        print(f"✗ Requirement file not found: {p}", file=sys.stderr)
        sys.exit(1)
    return p


def resolve_req_file(name: str) -> str:
    """Resolve requirement file name and verify it exists in JSONL."""
    name = name.replace(".md", "")
    reqs = state_db.list_requirements(name)
    if not reqs:
        print(
            f"✗ Requirement file '{name}' not found in JSONL. "
            f"If you have legacy .md files, run 'migrate' first.",
            file=sys.stderr,
        )
        sys.exit(1)
    return name


def cmd_criteria_add(args) -> None:
    state_db.ensure_state_dir()
    req_name = resolve_req_file(args.req_file)
    reqs = state_db.list_requirements(req_name)
    req_nos = {r["req_no"] for r in reqs}
    if args.req_no not in req_nos:
        print(
            f"✗ Requirement {args.req_no} not found in {req_name}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        entry = state_db.add_criteria(
            req_name,
            args.req_no,
            args.criterion,
            getattr(args, "by", "agent") or "agent",
        )
    except ValueError as e:
        print(f"✗ {e}", file=sys.stderr)
        sys.exit(1)

    print(
        f"✓ Added {entry['id']}: "
        f"[{entry['requirement']}#{entry['req_no']}] {entry['criterion']}"
    )


def cmd_criteria_list(args) -> None:
    state_db.ensure_state_dir()
    p = state_db.get_paths()
    criteria = state_db.read_jsonl(p.criteria_file)

    if args.req_file:
        req_name = args.req_file.replace(".md", "")
        criteria = [c for c in criteria if c["requirement"] == req_name]
    if args.req_no is not None:
        criteria = [c for c in criteria if c["req_no"] == args.req_no]

    if not criteria:
        print("(no criteria found)")
        return

    # Build requirement text lookup from JSONL for stale detection
    reqs = state_db.list_requirements()
    req_texts = {(r["file"], r["req_no"]): r["text"] for r in reqs}

    for c in criteria:
        stale = ""
        current_text = req_texts.get((c["requirement"], c["req_no"]), "")
        if current_text and state_db.hash_text(current_text) != c.get("req_text_hash", ""):
            stale = " ⚠️ stale"
        print(
            f"  {c['id']}: [{c['requirement']}#{c['req_no']}] "
            f"{c['criterion']}{stale}"
        )


def cmd_verify(args) -> None:
    state_db.ensure_state_dir()
    try:
        entry = state_db.verify(
            args.criteria_id,
            args.status,
            args.detail or "",
            args.limitation or "",
            args.by or "agent",
        )
    except ValueError as e:
        print(f"✗ {e}", file=sys.stderr)
        sys.exit(1)

    print(
        f"✓ Recorded {entry['id']}: {args.criteria_id} → {args.status}"
        + (f" ({args.limitation})" if args.limitation else "")
    )


def cmd_approve(args) -> None:
    state_db.ensure_state_dir()
    try:
        state_db.approve(args.verification_id, args.decision, args.comment or "")
    except ValueError as e:
        print(f"✗ {e}", file=sys.stderr)
        sys.exit(1)
    print(f"✓ {args.decision.capitalize()} {args.verification_id}")


def cmd_regress(args) -> None:
    state_db.ensure_state_dir()
    req_name = resolve_req_file(args.req_file)
    p = state_db.get_paths()
    criteria = state_db.read_jsonl(p.criteria_file)
    matching = [
        c
        for c in criteria
        if c["requirement"] == req_name and c["req_no"] == args.req_no
    ]

    if args.criteria_id:
        target = next((c for c in criteria if c["id"] == args.criteria_id), None)
        if target is None:
            print(f"✗ Criteria {args.criteria_id} not found", file=sys.stderr)
            sys.exit(1)
        if target["requirement"] != req_name or target["req_no"] != args.req_no:
            print(
                f"✗ Criteria {args.criteria_id} belongs to "
                f"{target['requirement']}#{target['req_no']}, "
                f"not {req_name}#{args.req_no}",
                file=sys.stderr,
            )
            sys.exit(1)
    elif len(matching) > 1:
        print(
            f"✗ Multiple criteria for {req_name}#{args.req_no}. "
            f"Specify one with --criteria-id:",
            file=sys.stderr,
        )
        for c in matching:
            print(f"  {c['id']}: {c['criterion']}", file=sys.stderr)
        sys.exit(1)

    auto_created = not args.criteria_id and not matching
    try:
        entry = state_db.regress(
            req_name,
            args.req_no,
            args.detail,
            args.criteria_id,
            args.by or "agent",
        )
    except ValueError as e:
        print(f"✗ {e}", file=sys.stderr)
        sys.exit(1)

    if auto_created:
        print(f"  (auto-created {entry['criteria_id']} from requirement text)")
    print(f"✓ Regression {entry['id']}: [{req_name}#{args.req_no}] {args.detail}")


def cmd_status(args) -> None:
    state_db.ensure_state_dir()
    rows = state_db.query_full_status(args.req_file)
    summary = state_db.compute_summary(rows)

    requirements = state_db.list_requirements(args.req_file)
    requirements_by_file: dict[str, list[dict]] = {}
    for req in requirements:
        requirements_by_file.setdefault(req["file"], []).append(req)
    for reqs in requirements_by_file.values():
        reqs.sort(key=lambda r: r["req_no"])

    rows_by_key: dict[tuple[str, int], list[dict]] = {}
    for row in rows:
        rows_by_key.setdefault((row["requirement"], row["req_no"]), []).append(row)

    req_names = (
        [args.req_file.replace(".md", "")]
        if args.req_file
        else sorted(requirements_by_file.keys())
    )

    total_reqs = 0
    with_criteria = 0
    no_criteria = 0

    for req_name in req_names:
        req_items = requirements_by_file.get(req_name, [])
        if not req_items:
            continue

        print(f"\n{'=' * 50}")
        print(f"  {req_name}.md")
        print(f"{'=' * 50}")

        for req in req_items:
            req_no = req["req_no"]
            req_text = req["text"]
            total_reqs += 1

            print(f"\n  {req_no}. {req_text}")

            clist = rows_by_key.get((req_name, req_no), [])
            if not clist:
                no_criteria += 1
                print("      ❓ No acceptance criteria defined")
                continue

            with_criteria += 1
            for c in clist:
                stale = " ⚠️ STALE" if c.get("is_stale") else ""
                if c.get("v_id") is None:
                    print(f"      📋 {c['criteria_id']}: {c['criterion']}{stale}")
                    print("         ❓ Not yet verified")
                    continue

                status_icon = {
                    "passed": "✅",
                    "failed": "❌",
                    "conditional": "⚠️",
                    "regression": "🔴",
                }.get(c["v_status"], "❓")

                if c.get("a_decision") == "approved":
                    approval_text = " — approved"
                elif c.get("a_decision"):
                    approval_text = f" — rejected: {c.get('a_comment', '')}"
                else:
                    approval_text = " — pending review"

                print(f"      📋 {c['criteria_id']}: {c['criterion']}{stale}")
                detail_str = (
                    f" ({c['v_limitation']})"
                    if c.get("v_limitation")
                    else (
                        f" ({c['v_detail'][:60]})"
                        if c.get("v_detail")
                        else ""
                    )
                )
                print(
                    f"         {status_icon} {c['v_id']}: "
                    f"{c['v_status']}{detail_str}{approval_text}"
                )

    print(f"\n{'─' * 50}")
    print(
        f"  Requirements: {total_reqs}  |  "
        f"With criteria: {with_criteria}  |  "
        f"No criteria: {no_criteria}"
    )
    print(
        f"  Approved: {summary['approved']}  |  "
        f"Pending review: {summary['pending'] + summary['regression']}  |  "
        f"Failed: {summary['failed']}  |  "
        f"Regressions: {summary['regression']}"
    )
    print(f"{'─' * 50}")


def cmd_req_add(args) -> None:
    state_db.ensure_state_dir()
    entry = state_db.add_requirement(args.file, args.text, args.by or "user")
    print(f"✓ Added {entry['id']}: [{entry['file']}#{entry['req_no']}] {entry['text']}")


def cmd_req_list(args) -> None:
    state_db.ensure_state_dir()
    reqs = state_db.list_requirements(args.file)
    if not reqs:
        print("(no requirements found)")
        return
    for r in reqs:
        print(f"  {r['id']}: [{r['file']}#{r['req_no']}] {r['text']}")


def cmd_spec_add(args) -> None:
    state_db.ensure_state_dir()
    entry = state_db.add_spec(
        args.requirement,
        args.title,
        args.body,
        args.commits,
        args.files,
        args.by or "agent",
    )
    print(f"✓ Added {entry['id']}: [{entry['requirement']}] {entry['title']}")


def cmd_migrate(args) -> None:
    state_db.ensure_state_dir()
    p = state_db.get_paths()

    if args.req_file:
        files = [_resolve_req_file_md(args.req_file)]
    else:
        if not p.requirements_dir.exists():
            print("  ⏭ No legacy requirement files found. Nothing to migrate.")
            return
        files = sorted(p.requirements_dir.glob("*.md"))

    existing_criteria = state_db.read_jsonl(p.criteria_file)
    existing_reqs = {(c["requirement"], c["req_no"]) for c in existing_criteria}

    total_criteria = 0
    total_verifications = 0
    total_approvals = 0

    for req_path in files:
        text = req_path.read_text(encoding="utf-8-sig")
        fm = extract_front_matter(text)
        if fm is None or "passed_stutas" not in fm:
            print(f"  ⏭ {req_path.name}: no passed_stutas to migrate")
            continue

        ps = fm["passed_stutas"]
        if ps is None:
            print(f"  ⏭ {req_path.name}: passed_stutas is empty")
            continue

        body = state_db.parse_requirement_body(req_path)
        req_name = req_path.stem

        print(f"\n  Migrating {req_path.name}...")

        date_entries: list[tuple[str, int, str]] = []
        for date_key, entries in ps.items():
            if isinstance(date_key, __import__("datetime").date):
                date_str = date_key.isoformat()
            else:
                date_str = str(date_key)

            if not isinstance(entries, list):
                continue

            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                for req_no_raw, status_raw in entry.items():
                    req_no = int(req_no_raw)
                    status_str = str(status_raw)
                    date_entries.append((date_str, req_no, status_str))

        date_entries.sort(key=lambda x: (x[0], x[1]))
        created_criteria: dict[int, str] = {}

        for date_str, req_no, status_str in date_entries:
            if (req_name, req_no) not in existing_reqs and req_no not in created_criteria:
                req_text = body.get(req_no, f"Requirement {req_no}")
                cid = state_db.next_id(p.criteria_file, "ac")
                c_entry = {
                    "id": cid,
                    "requirement": req_name,
                    "req_no": req_no,
                    "criterion": req_text,
                    "req_text_hash": state_db.hash_text(req_text),
                    "created_at": f"{date_str}T00:00:00+00:00",
                    "created_by": "migration",
                }
                state_db.append_jsonl(p.criteria_file, c_entry)
                created_criteria[req_no] = cid
                existing_reqs.add((req_name, req_no))
                total_criteria += 1

            criteria_id = created_criteria.get(req_no, "")
            if not criteria_id:
                for c in state_db.read_jsonl(p.criteria_file):
                    if c["requirement"] == req_name and c["req_no"] == req_no:
                        criteria_id = c["id"]
                        break

            is_regression = status_str.startswith("REGRESSION:")
            if is_regression:
                v_status = "regression"
                v_detail = status_str[len("REGRESSION:") :].strip()
                v_limitation = ""
            elif status_str == "passed":
                v_status = "passed"
                v_detail = ""
                v_limitation = ""
            else:
                v_status = "conditional"
                v_detail = status_str
                v_limitation = status_str

            vid = state_db.next_id(p.verifications_file, "v")
            v_entry = {
                "id": vid,
                "criteria_id": criteria_id,
                "requirement": req_name,
                "req_no": req_no,
                "status": v_status,
                "detail": v_detail,
                "limitation": v_limitation,
                "verified_at": f"{date_str}T00:00:00+00:00",
                "verified_by": "migration",
            }
            state_db.append_jsonl(p.verifications_file, v_entry)
            total_verifications += 1

            if v_status == "passed":
                aid = state_db.next_id(p.approvals_file, "a")
                a_entry = {
                    "id": aid,
                    "verification_id": vid,
                    "decision": "approved",
                    "comment": "auto-approved (migration)",
                    "decided_at": state_db.now_iso(),
                }
                state_db.append_jsonl(p.approvals_file, a_entry)
                total_approvals += 1

        print(f"    ✓ {req_path.name} done")

    req_mig = state_db.migrate_requirements_md()
    spec_mig = state_db.migrate_specs_md()

    print(f"\n  Migration complete:")
    print(f"    Criteria created: {total_criteria}")
    print(f"    Verifications created: {total_verifications}")
    print(f"    Auto-approvals created: {total_approvals}")
    print(f"    Requirements migrated: {req_mig['migrated']}")
    print(f"    Specs migrated: {spec_mig['migrated']}")


def cmd_validate(args) -> None:
    errors = state_db.validate_state()
    if not errors:
        print("✓ All state files valid.")
        return
    total = sum(len(e) for e in errors.values())
    print(f"✗ {total} error(s) in {len(errors)} file(s):", file=sys.stderr)
    for source, errs in sorted(errors.items()):
        for e in errs:
            print(f"  {source}: {e}", file=sys.stderr)
    sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="req_tool",
        description="Requirements state management CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    crit = sub.add_parser("criteria", help="Manage acceptance criteria")
    crit_sub = crit.add_subparsers(dest="crit_action", required=True)

    crit_add = crit_sub.add_parser("add", help="Add acceptance criterion")
    crit_add.add_argument("req_file", help="Requirement file (e.g. 0001)")
    crit_add.add_argument("req_no", type=int, help="Requirement number")
    crit_add.add_argument("criterion", help="Acceptance criterion text")
    crit_add.add_argument("--by", default="agent", help="Creator")

    crit_list = crit_sub.add_parser("list", help="List acceptance criteria")
    crit_list.add_argument("req_file", nargs="?", help="Filter by file")
    crit_list.add_argument("--req-no", type=int, help="Filter by req number")

    ver = sub.add_parser("verify", help="Record verification result")
    ver.add_argument("criteria_id", help="Criteria ID (e.g. ac-1)")
    ver.add_argument(
        "status",
        choices=["passed", "failed", "conditional"],
        help="Verification status",
    )
    ver.add_argument("--detail", help="Verification detail")
    ver.add_argument("--limitation", help="Limitation for conditional status")
    ver.add_argument("--by", default="agent", help="Verifier name")

    appr = sub.add_parser("approve", help="Approve/reject a verification")
    appr.add_argument("verification_id", help="Verification ID (e.g. v-1)")
    appr.add_argument(
        "decision",
        choices=["approved", "rejected"],
        help="Approval decision",
    )
    appr.add_argument("--comment", help="Comment")

    reg = sub.add_parser("regress", help="Record regression")
    reg.add_argument("req_file", help="Requirement file (e.g. 0001)")
    reg.add_argument("req_no", type=int, help="Requirement number")
    reg.add_argument("--detail", required=True, help="Regression detail")
    reg.add_argument(
        "--criteria-id",
        help="Target criteria ID (required when multiple criteria exist)",
    )
    reg.add_argument("--by", default="agent", help="Reporter name")

    st = sub.add_parser("status", help="Show verification status")
    st.add_argument("req_file", nargs="?", help="Filter by file")

    mig = sub.add_parser("migrate", help="Migrate from frontmatter")
    mig.add_argument("req_file", nargs="?", help="Specific file to migrate")

    req = sub.add_parser("req", help="Manage requirements")
    req_sub = req.add_subparsers(dest="req_action", required=True)

    req_add = req_sub.add_parser("add", help="Add requirement")
    req_add.add_argument("file", help="Requirement file (e.g. 0001)")
    req_add.add_argument("text", help="Requirement text")
    req_add.add_argument("--by", default="user", help="Creator")

    req_list = req_sub.add_parser("list", help="List requirements")
    req_list.add_argument("file", nargs="?", help="Filter by file")

    spec = sub.add_parser("spec", help="Manage specs")
    spec_sub = spec.add_subparsers(dest="spec_action", required=True)

    spec_add = spec_sub.add_parser("add", help="Add spec")
    spec_add.add_argument("requirement", help="Requirement file (e.g. 0001)")
    spec_add.add_argument("title", help="Spec title")
    spec_add.add_argument("--body", required=True, help="Spec body")
    spec_add.add_argument("--commits", nargs="*", help="Related commits")
    spec_add.add_argument("--files", nargs="*", help="Related files")
    spec_add.add_argument("--by", default="agent", help="Creator")

    sub.add_parser("validate", help="Validate state files")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "criteria":
            if args.crit_action == "add":
                cmd_criteria_add(args)
            elif args.crit_action == "list":
                cmd_criteria_list(args)
        elif args.command == "verify":
            cmd_verify(args)
        elif args.command == "approve":
            cmd_approve(args)
        elif args.command == "regress":
            cmd_regress(args)
        elif args.command == "status":
            cmd_status(args)
        elif args.command == "migrate":
            cmd_migrate(args)
        elif args.command == "req":
            if args.req_action == "add":
                cmd_req_add(args)
            elif args.req_action == "list":
                cmd_req_list(args)
        elif args.command == "spec":
            if args.spec_action == "add":
                cmd_spec_add(args)
        elif args.command == "validate":
            cmd_validate(args)
        return 0
    except RuntimeError as e:
        if str(e) == "Cannot find repository root":
            print("✗ Cannot find repository root", file=sys.stderr)
            return 1
        print(f"✗ Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
