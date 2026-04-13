# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml"]
# ///
"""
Requirements state management CLI.

This tool is the sole interface for managing requirement verification state.
State is stored as append-only JSONL files.

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
"""

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def _find_repo_root() -> Path:
    """Find repository root via git, with fallback to .git directory walk."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    # Fallback: walk up from this file looking for .git
    p = Path(__file__).resolve().parent
    while p != p.parent:
        if (p / ".git").exists():
            return p
        p = p.parent
    print("✗ Cannot find repository root", file=sys.stderr)
    sys.exit(1)


REPO_ROOT = _find_repo_root()
LOCAL_DIR = REPO_ROOT / ".local"
STATE_DIR = LOCAL_DIR / "state"
REQUIREMENTS_DIR = LOCAL_DIR / "requirements"
CRITERIA_FILE = STATE_DIR / "acceptance_criteria.jsonl"
VERIFICATIONS_FILE = STATE_DIR / "verifications.jsonl"
APPROVALS_FILE = STATE_DIR / "approvals.jsonl"


# ---------------------------------------------------------------------------
# Core I/O
# ---------------------------------------------------------------------------


def ensure_state_dir():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    for f in (CRITERIA_FILE, VERIFICATIONS_FILE, APPROVALS_FILE):
        if not f.exists():
            f.touch()


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


def append_jsonl(path: Path, entry: dict):
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")


def next_id(path: Path, prefix: str) -> str:
    entries = read_jsonl(path)
    max_n = 0
    for e in entries:
        eid = e.get("id", "")
        if eid.startswith(f"{prefix}-"):
            try:
                n = int(eid[len(prefix) + 1 :])
                max_n = max(max_n, n)
            except ValueError:
                pass
    return f"{prefix}-{max_n + 1}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Requirement file parsing
# ---------------------------------------------------------------------------


def parse_requirement_body(path: Path) -> dict[int, str]:
    """Parse requirement body, returning {req_no: first_line_text}."""
    text = path.read_text(encoding="utf-8-sig")
    # Skip frontmatter
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            text = text[end + 3 :]
    result = {}
    for m in re.finditer(r"^(\d+)\.\s+(.+)$", text, re.MULTILINE):
        result[int(m.group(1))] = m.group(2).strip()
    return result


def extract_front_matter(text: str) -> dict | None:
    match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return None
    try:
        data = yaml.safe_load(match.group(1))
        return data if data else {}
    except yaml.YAMLError:
        return None


def resolve_req_file(name: str) -> Path:
    """Resolve requirement file by number (e.g. '0001') or path."""
    if not name.endswith(".md"):
        name = f"{name}.md"
    p = REQUIREMENTS_DIR / name
    if not p.exists():
        print(f"✗ Requirement file not found: {p}", file=sys.stderr)
        sys.exit(1)
    return p


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_criteria_add(args):
    ensure_state_dir()
    req_path = resolve_req_file(args.req_file)
    req_no = args.req_no
    criterion = args.criterion

    body = parse_requirement_body(req_path)
    req_text = body.get(req_no)
    if req_text is None:
        print(
            f"✗ Requirement {req_no} not found in {req_path.name}", file=sys.stderr
        )
        sys.exit(1)

    new_id = next_id(CRITERIA_FILE, "ac")
    entry = {
        "id": new_id,
        "requirement": req_path.stem,
        "req_no": req_no,
        "criterion": criterion,
        "req_text_hash": hash_text(req_text),
        "created_at": now_iso(),
        "created_by": getattr(args, "by", "agent") or "agent",
    }
    append_jsonl(CRITERIA_FILE, entry)
    print(f"✓ Added {new_id}: [{req_path.stem}#{req_no}] {criterion}")


def cmd_criteria_list(args):
    ensure_state_dir()
    criteria = read_jsonl(CRITERIA_FILE)

    if args.req_file:
        req_name = args.req_file.replace(".md", "")
        criteria = [c for c in criteria if c["requirement"] == req_name]
    if args.req_no is not None:
        criteria = [c for c in criteria if c["req_no"] == args.req_no]

    if not criteria:
        print("(no criteria found)")
        return

    for c in criteria:
        stale = ""
        req_path = REQUIREMENTS_DIR / f"{c['requirement']}.md"
        if req_path.exists():
            body = parse_requirement_body(req_path)
            current_text = body.get(c["req_no"], "")
            if hash_text(current_text) != c.get("req_text_hash", ""):
                stale = " ⚠️ stale"
        print(
            f"  {c['id']}: [{c['requirement']}#{c['req_no']}] "
            f"{c['criterion']}{stale}"
        )


def cmd_verify(args):
    ensure_state_dir()
    criteria = read_jsonl(CRITERIA_FILE)
    target = next((c for c in criteria if c["id"] == args.criteria_id), None)
    if target is None:
        print(f"✗ Criteria {args.criteria_id} not found", file=sys.stderr)
        sys.exit(1)

    if args.status not in ("passed", "failed", "conditional"):
        print(
            f"✗ Invalid status '{args.status}'. Use: passed, failed, conditional",
            file=sys.stderr,
        )
        sys.exit(1)

    new_id = next_id(VERIFICATIONS_FILE, "v")
    entry = {
        "id": new_id,
        "criteria_id": args.criteria_id,
        "requirement": target["requirement"],
        "req_no": target["req_no"],
        "status": args.status,
        "detail": args.detail or "",
        "limitation": args.limitation or "",
        "verified_at": now_iso(),
        "verified_by": args.by or "agent",
    }
    append_jsonl(VERIFICATIONS_FILE, entry)
    print(
        f"✓ Recorded {new_id}: {args.criteria_id} → {args.status}"
        + (f" ({args.limitation})" if args.limitation else "")
    )


def cmd_approve(args):
    ensure_state_dir()
    verifications = read_jsonl(VERIFICATIONS_FILE)
    target = next((v for v in verifications if v["id"] == args.verification_id), None)
    if target is None:
        print(
            f"✗ Verification {args.verification_id} not found", file=sys.stderr
        )
        sys.exit(1)

    if args.decision not in ("approved", "rejected"):
        print(
            f"✗ Invalid decision '{args.decision}'. Use: approved, rejected",
            file=sys.stderr,
        )
        sys.exit(1)

    new_id = next_id(APPROVALS_FILE, "a")
    entry = {
        "id": new_id,
        "verification_id": args.verification_id,
        "decision": args.decision,
        "comment": args.comment or "",
        "decided_at": now_iso(),
    }
    append_jsonl(APPROVALS_FILE, entry)
    print(f"✓ {args.decision.capitalize()} {args.verification_id}")


def cmd_regress(args):
    ensure_state_dir()
    req_path = resolve_req_file(args.req_file)
    req_no = args.req_no

    # Find criteria for this req_no, or create default
    criteria = read_jsonl(CRITERIA_FILE)
    matching = [
        c
        for c in criteria
        if c["requirement"] == req_path.stem and c["req_no"] == req_no
    ]

    criteria_id = ""
    if args.criteria_id:
        # Explicit criteria ID — validate it exists
        target = next((c for c in criteria if c["id"] == args.criteria_id), None)
        if target is None:
            print(f"✗ Criteria {args.criteria_id} not found", file=sys.stderr)
            sys.exit(1)
        if target["requirement"] != req_path.stem or target["req_no"] != req_no:
            print(
                f"✗ Criteria {args.criteria_id} belongs to "
                f"{target['requirement']}#{target['req_no']}, not {req_path.stem}#{req_no}",
                file=sys.stderr,
            )
            sys.exit(1)
        criteria_id = args.criteria_id
    elif matching:
        if len(matching) > 1:
            print(
                f"✗ Multiple criteria for {req_path.stem}#{req_no}. "
                f"Specify one with --criteria-id:",
                file=sys.stderr,
            )
            for c in matching:
                print(f"  {c['id']}: {c['criterion']}", file=sys.stderr)
            sys.exit(1)
        criteria_id = matching[0]["id"]
    else:
        # Auto-create default criterion
        body = parse_requirement_body(req_path)
        req_text = body.get(req_no, f"Requirement {req_no}")
        cid = next_id(CRITERIA_FILE, "ac")
        c_entry = {
            "id": cid,
            "requirement": req_path.stem,
            "req_no": req_no,
            "criterion": req_text,
            "req_text_hash": hash_text(req_text),
            "created_at": now_iso(),
            "created_by": "migration",
        }
        append_jsonl(CRITERIA_FILE, c_entry)
        criteria_id = cid
        print(f"  (auto-created {cid} from requirement text)")

    new_id = next_id(VERIFICATIONS_FILE, "v")
    entry = {
        "id": new_id,
        "criteria_id": criteria_id,
        "requirement": req_path.stem,
        "req_no": req_no,
        "status": "regression",
        "detail": args.detail,
        "limitation": "",
        "verified_at": now_iso(),
        "verified_by": args.by or "agent",
    }
    append_jsonl(VERIFICATIONS_FILE, entry)
    print(f"✓ Regression {new_id}: [{req_path.stem}#{req_no}] {args.detail}")


def cmd_status(args):
    ensure_state_dir()
    criteria = read_jsonl(CRITERIA_FILE)
    verifications = read_jsonl(VERIFICATIONS_FILE)
    approvals = read_jsonl(APPROVALS_FILE)

    # Index approvals by verification_id (latest wins)
    approval_map: dict[str, dict] = {}
    for a in approvals:
        approval_map[a["verification_id"]] = a

    # Index verifications by criteria_id (latest wins)
    verif_map: dict[str, dict] = {}
    for v in verifications:
        cid = v.get("criteria_id", "")
        if cid:
            verif_map[cid] = v

    # Group criteria by requirement
    req_criteria: dict[str, list[dict]] = {}
    for c in criteria:
        req_criteria.setdefault(c["requirement"], []).append(c)

    # Determine which requirements to show
    if args.req_file:
        req_names = [args.req_file.replace(".md", "")]
    else:
        req_names = sorted(
            set(c["requirement"] for c in criteria)
            | set(
                p.stem
                for p in REQUIREMENTS_DIR.glob("*.md")
                if p.stem != "0000"
            )
        )

    total_reqs = 0
    with_criteria = 0
    verified_approved = 0
    verified_pending = 0
    failed_count = 0
    regression_count = 0
    no_criteria = 0

    for req_name in req_names:
        req_path = REQUIREMENTS_DIR / f"{req_name}.md"
        if not req_path.exists():
            continue
        body = parse_requirement_body(req_path)
        my_criteria = req_criteria.get(req_name, [])
        criteria_by_reqno: dict[int, list[dict]] = {}
        for c in my_criteria:
            criteria_by_reqno.setdefault(c["req_no"], []).append(c)

        print(f"\n{'=' * 50}")
        print(f"  {req_name}.md")
        print(f"{'=' * 50}")

        for req_no in sorted(body.keys()):
            total_reqs += 1
            req_text = body[req_no]
            print(f"\n  {req_no}. {req_text}")

            clist = criteria_by_reqno.get(req_no, [])
            if not clist:
                no_criteria += 1
                print("      ❓ No acceptance criteria defined")
                continue

            with_criteria += 1
            for c in clist:
                stale = ""
                if hash_text(body.get(c["req_no"], "")) != c.get(
                    "req_text_hash", ""
                ):
                    stale = " ⚠️ STALE"

                v = verif_map.get(c["id"])
                if v is None:
                    print(
                        f"      📋 {c['id']}: {c['criterion']}{stale}"
                    )
                    print("         ❓ Not yet verified")
                else:
                    a = approval_map.get(v["id"])
                    status_icon = {
                        "passed": "✅",
                        "failed": "❌",
                        "conditional": "⚠️",
                        "regression": "🔴",
                    }.get(v["status"], "❓")

                    approval_text = ""
                    if a:
                        if a["decision"] == "approved":
                            approval_text = " — approved"
                            verified_approved += 1
                        else:
                            approval_text = f" — rejected: {a.get('comment', '')}"
                    else:
                        approval_text = " — pending review"
                        verified_pending += 1

                    if v["status"] == "failed":
                        failed_count += 1
                    elif v["status"] == "regression":
                        regression_count += 1

                    print(
                        f"      📋 {c['id']}: {c['criterion']}{stale}"
                    )
                    detail_str = (
                        f" ({v['limitation']})"
                        if v.get("limitation")
                        else (
                            f" ({v['detail'][:60]})"
                            if v.get("detail")
                            else ""
                        )
                    )
                    print(
                        f"         {status_icon} {v['id']}: "
                        f"{v['status']}{detail_str}{approval_text}"
                    )

    print(f"\n{'─' * 50}")
    print(
        f"  Requirements: {total_reqs}  |  "
        f"With criteria: {with_criteria}  |  "
        f"No criteria: {no_criteria}"
    )
    print(
        f"  Approved: {verified_approved}  |  "
        f"Pending review: {verified_pending}  |  "
        f"Failed: {failed_count}  |  "
        f"Regressions: {regression_count}"
    )
    print(f"{'─' * 50}")


def cmd_migrate(args):
    ensure_state_dir()

    if args.req_file:
        files = [resolve_req_file(args.req_file)]
    else:
        files = sorted(
            p
            for p in REQUIREMENTS_DIR.glob("*.md")
            if p.stem != "0000"
        )

    existing_criteria = read_jsonl(CRITERIA_FILE)
    existing_reqs = {
        (c["requirement"], c["req_no"]) for c in existing_criteria
    }

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

        body = parse_requirement_body(req_path)
        req_name = req_path.stem

        print(f"\n  Migrating {req_path.name}...")

        # Collect all entries sorted by date
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

        # Sort by date, then req_no
        date_entries.sort(key=lambda x: (x[0], x[1]))

        # Track criteria we create per req_no
        created_criteria: dict[int, str] = {}

        for date_str, req_no, status_str in date_entries:
            # Create criterion if not exists
            if (req_name, req_no) not in existing_reqs and req_no not in created_criteria:
                req_text = body.get(req_no, f"Requirement {req_no}")
                cid = next_id(CRITERIA_FILE, "ac")
                c_entry = {
                    "id": cid,
                    "requirement": req_name,
                    "req_no": req_no,
                    "criterion": req_text,
                    "req_text_hash": hash_text(req_text),
                    "created_at": f"{date_str}T00:00:00+00:00",
                    "created_by": "migration",
                }
                append_jsonl(CRITERIA_FILE, c_entry)
                created_criteria[req_no] = cid
                existing_reqs.add((req_name, req_no))
                total_criteria += 1

            criteria_id = created_criteria.get(req_no, "")
            if not criteria_id:
                # Find existing criteria
                for c in read_jsonl(CRITERIA_FILE):
                    if c["requirement"] == req_name and c["req_no"] == req_no:
                        criteria_id = c["id"]
                        break

            # Determine status and detail
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
                # Conditional or detailed pass
                v_status = "conditional"
                v_detail = status_str
                v_limitation = status_str

            vid = next_id(VERIFICATIONS_FILE, "v")
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
            append_jsonl(VERIFICATIONS_FILE, v_entry)
            total_verifications += 1

            # Auto-approve only "passed" entries (conditional/regression need user review)
            if v_status == "passed":
                aid = next_id(APPROVALS_FILE, "a")
                a_entry = {
                    "id": aid,
                    "verification_id": vid,
                    "decision": "approved",
                    "comment": "auto-approved (migration)",
                    "decided_at": now_iso(),
                }
                append_jsonl(APPROVALS_FILE, a_entry)
                total_approvals += 1

        print(f"    ✓ {req_path.name} done")

    print(f"\n  Migration complete:")
    print(f"    Criteria created: {total_criteria}")
    print(f"    Verifications created: {total_verifications}")
    print(f"    Auto-approvals created: {total_approvals}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="req_tool",
        description="Requirements state management CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # criteria
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

    # verify
    ver = sub.add_parser("verify", help="Record verification result")
    ver.add_argument("criteria_id", help="Criteria ID (e.g. ac-1)")
    ver.add_argument(
        "status", choices=["passed", "failed", "conditional"],
        help="Verification status",
    )
    ver.add_argument("--detail", help="Verification detail")
    ver.add_argument("--limitation", help="Limitation for conditional status")
    ver.add_argument("--by", default="agent", help="Verifier name")

    # approve
    appr = sub.add_parser("approve", help="Approve/reject a verification")
    appr.add_argument("verification_id", help="Verification ID (e.g. v-1)")
    appr.add_argument(
        "decision", choices=["approved", "rejected"],
        help="Approval decision",
    )
    appr.add_argument("--comment", help="Comment")

    # regress
    reg = sub.add_parser("regress", help="Record regression")
    reg.add_argument("req_file", help="Requirement file (e.g. 0001)")
    reg.add_argument("req_no", type=int, help="Requirement number")
    reg.add_argument("--detail", required=True, help="Regression detail")
    reg.add_argument("--criteria-id", help="Target criteria ID (required when multiple criteria exist)")
    reg.add_argument("--by", default="agent", help="Reporter name")

    # status
    st = sub.add_parser("status", help="Show verification status")
    st.add_argument("req_file", nargs="?", help="Filter by file")

    # migrate
    mig = sub.add_parser("migrate", help="Migrate from frontmatter")
    mig.add_argument("req_file", nargs="?", help="Specific file to migrate")

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
        return 0
    except Exception as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
