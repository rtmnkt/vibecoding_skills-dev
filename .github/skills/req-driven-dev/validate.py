# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml"]
# ///
"""
Requirements / specs / state validator.

Validates:
  - .local/requirements/*.md  — body-only format (numbered requirements)
  - .local/specs/*.md          — YAML front matter (requirement, commits, files)
  - .local/state/*.jsonl       — JSONL schema validation

Usage:
    uv run .github/skills/req-driven-dev/validate.py          # validate all
    uv run .github/skills/req-driven-dev/validate.py FILE...  # validate specific files
"""

import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


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
    p = Path(__file__).resolve().parent
    while p != p.parent:
        if (p / ".git").exists():
            return p
        p = p.parent
    print("✗ Cannot find repository root", file=sys.stderr)
    sys.exit(1)


REPO_ROOT = _find_repo_root()
LOCAL_DIR = REPO_ROOT / ".local"


def extract_front_matter(text: str) -> tuple[dict | None, str | None]:
    match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return None, "Missing YAML front matter (--- delimiters)"
    try:
        data = yaml.safe_load(match.group(1))
        return data if data else {}, None
    except yaml.YAMLError as e:
        return None, f"Invalid YAML: {e}"


def validate_requirement(path: Path) -> list[str]:
    errors = []
    text = path.read_text(encoding="utf-8-sig")

    # New format: body-only (no frontmatter required)
    # If frontmatter exists, it's legacy — warn but don't fail
    body = text
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            fm_text = text[3:end].strip()
            if fm_text:
                errors.append(
                    "Legacy frontmatter detected. "
                    "Run 'uv run .github/skills/req-driven-dev/req_tool.py migrate' to move state to JSONL"
                )
            body = text[end + 3 :].strip()

    # Validate body has at least one numbered requirement
    if not re.search(r"^\d+\.\s+\S", body, re.MULTILINE):
        errors.append("No numbered requirements found (expected: '10. description')")

    return errors


def validate_spec(path: Path) -> list[str]:
    errors = []
    text = path.read_text(encoding="utf-8-sig")

    fm, err = extract_front_matter(text)
    if err:
        errors.append(err)
        return errors

    for key, expected_type in (
        ("requirement", str),
        ("commits", list),
        ("files", list),
    ):
        if key not in fm:
            errors.append(f"Missing required key '{key}'")
        elif not isinstance(fm[key], expected_type):
            errors.append(
                f"'{key}' must be {expected_type.__name__}, got {type(fm[key]).__name__}"
            )

    return errors


JSONL_SCHEMAS: dict[str, dict[str, type]] = {
    "acceptance_criteria": {
        "id": str,
        "requirement": str,
        "req_no": int,
        "criterion": str,
        "created_at": str,
    },
    "verifications": {
        "id": str,
        "criteria_id": str,
        "requirement": str,
        "req_no": int,
        "status": str,
        "verified_at": str,
    },
    "approvals": {
        "id": str,
        "verification_id": str,
        "decision": str,
        "decided_at": str,
    },
}

VALID_STATUSES = {"passed", "failed", "conditional", "regression"}
VALID_DECISIONS = {"approved", "rejected"}


def validate_jsonl(path: Path) -> list[str]:
    errors = []
    schema_name = path.stem
    schema = JSONL_SCHEMAS.get(schema_name)
    if schema is None:
        return []  # Unknown JSONL file, skip

    text = path.read_text(encoding="utf-8")
    ids_seen: set[str] = set()

    for line_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue

        try:
            entry = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"Line {line_no}: Invalid JSON: {e}")
            continue

        if not isinstance(entry, dict):
            errors.append(f"Line {line_no}: Expected JSON object, got {type(entry).__name__}")
            continue

        for key, expected_type in schema.items():
            if key not in entry:
                errors.append(f"Line {line_no}: Missing required key '{key}'")
            elif not isinstance(entry[key], expected_type):
                errors.append(
                    f"Line {line_no}: '{key}' must be {expected_type.__name__}, "
                    f"got {type(entry[key]).__name__}"
                )

        # Check unique IDs
        eid = entry.get("id", "")
        if eid in ids_seen:
            errors.append(f"Line {line_no}: Duplicate ID '{eid}'")
        ids_seen.add(eid)

        # Check status values
        if schema_name == "verifications":
            status = entry.get("status", "")
            if status and status not in VALID_STATUSES:
                errors.append(
                    f"Line {line_no}: Invalid status '{status}' "
                    f"(expected: {', '.join(sorted(VALID_STATUSES))})"
                )

        if schema_name == "approvals":
            decision = entry.get("decision", "")
            if decision and decision not in VALID_DECISIONS:
                errors.append(
                    f"Line {line_no}: Invalid decision '{decision}' "
                    f"(expected: {', '.join(sorted(VALID_DECISIONS))})"
                )

    return errors


def main() -> int:
    all_errors: dict[str, list[str]] = {}

    if len(sys.argv) > 1:
        paths = [Path(p) for p in sys.argv[1:]]
    else:
        paths = []
        for subdir in ("requirements", "specs"):
            d = LOCAL_DIR / subdir
            if d.exists():
                paths.extend(sorted(d.glob("*.md")))
        # Also validate JSONL state files
        state_dir = LOCAL_DIR / "state"
        if state_dir.exists():
            paths.extend(sorted(state_dir.glob("*.jsonl")))

    for path in paths:
        if path.stem == "0000":
            continue

        parent = path.parent.name
        if parent == "requirements":
            errs = validate_requirement(path)
        elif parent == "specs":
            errs = validate_spec(path)
        elif parent == "state" and path.suffix == ".jsonl":
            errs = validate_jsonl(path)
        else:
            continue

        if errs:
            all_errors[str(path)] = errs

    if all_errors:
        total = sum(len(e) for e in all_errors.values())
        print(f"✗ {total} error(s) in {len(all_errors)} file(s):", file=sys.stderr)
        for filepath, errs in all_errors.items():
            for e in errs:
                print(f"  {filepath}: {e}", file=sys.stderr)
        return 1
    else:
        validated = sum(
            1 for p in paths if p.stem != "0000"
            and (p.parent.name in ("requirements", "specs", "state"))
        )
        print(f"✓ {validated} file(s) valid.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
