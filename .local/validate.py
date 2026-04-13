# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml"]
# ///
"""
.local/ front matter validator.

Usage:
    uv run .local/validate.py          # validate all
    uv run .local/validate.py FILE...  # validate specific files
"""

import datetime
import re
import sys
from pathlib import Path

import yaml

DOCS_ROOT = Path(__file__).parent


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

    fm, err = extract_front_matter(text)
    if err:
        errors.append(err)
        return errors

    if "passed_stutas" not in fm:
        errors.append("Missing 'passed_stutas' key")
        return errors

    ps = fm["passed_stutas"]
    if ps is None:
        return errors

    if not isinstance(ps, dict):
        errors.append(
            f"'passed_stutas' must be a date-keyed mapping, got {type(ps).__name__}: {ps!r}"
        )
        return errors

    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")

    for date_key, entries in ps.items():
        # PyYAML parses YYYY-MM-DD as datetime.date
        if isinstance(date_key, datetime.date):
            date_str = date_key.isoformat()
        else:
            date_str = str(date_key)

        if not date_pattern.match(date_str):
            errors.append(f"Invalid date key '{date_key}' (expected YYYY-MM-DD)")

        if not isinstance(entries, list):
            errors.append(
                f"Entries under '{date_str}' must be a list, got {type(entries).__name__}"
            )
            continue

        for entry in entries:
            if not isinstance(entry, dict):
                errors.append(f"Entry '{entry}' must be a mapping (reqno: status)")
                continue
            if len(entry) != 1:
                errors.append(
                    f"Each entry must have exactly one reqno:status pair, got {len(entry)}: {entry}"
                )
            for reqno, status in entry.items():
                if not isinstance(reqno, int):
                    errors.append(f"Requirement number '{reqno}' must be an integer")
                if not isinstance(status, str):
                    errors.append(
                        f"Status for reqno {reqno} must be a string, got {type(status).__name__}: {status!r}"
                    )

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


def main() -> int:
    all_errors: dict[str, list[str]] = {}

    if len(sys.argv) > 1:
        paths = [Path(p) for p in sys.argv[1:]]
    else:
        paths = []
        for subdir in ("requirements", "specs"):
            d = DOCS_ROOT / subdir
            if d.exists():
                paths.extend(sorted(d.glob("*.md")))

    for path in paths:
        if path.stem == "0000":
            continue

        parent = path.parent.name
        if parent == "requirements":
            errs = validate_requirement(path)
        elif parent == "specs":
            errs = validate_spec(path)
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
        validated = sum(1 for p in paths if p.stem != "0000")
        print(f"✓ {validated} file(s) valid.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
