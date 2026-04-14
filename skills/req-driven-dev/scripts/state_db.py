"""
Shared data layer for req-driven-dev skill.

Provides DuckDB-based reads, JSONL writes (sorted rewrite), and migration helpers.
Used by both req_tool.py (CLI) and webui.py (NiceGUI).

Architecture:
  - JSONL files are the durable source of truth (git-tracked).
  - Each operation loads JSONL → in-memory DuckDB → processes → rewrites JSONL sorted.
  - Schemas defined in schema.py; DuckDB tables created with explicit DDL.

This is a regular Python module (not a uv script).
Callers must have duckdb in their dependencies.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

import duckdb

import schema as _schema


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


class Paths(NamedTuple):
    repo_root: Path
    local_dir: Path
    state_dir: Path
    requirements_dir: Path
    specs_dir: Path
    criteria_file: Path
    verifications_file: Path
    approvals_file: Path
    requirements_file: Path  # NEW: requirements.jsonl
    specs_file: Path  # NEW: specs.jsonl


def find_repo_root() -> Path:
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
    raise RuntimeError("Cannot find repository root")


def get_paths(repo_root: Path | None = None) -> Paths:
    root = repo_root or find_repo_root()
    local = root / ".local"
    state = local / "skills_req-driven-dev"
    return Paths(
        repo_root=root,
        local_dir=local,
        state_dir=state,
        requirements_dir=local / "requirements",   # migration-only
        specs_dir=local / "specs",                  # migration-only
        criteria_file=state / "acceptance_criteria.jsonl",
        verifications_file=state / "verifications.jsonl",
        approvals_file=state / "approvals.jsonl",
        requirements_file=state / "requirements.jsonl",
        specs_file=state / "specs.jsonl",
    )


_paths: Paths | None = None


def _get_paths() -> Paths:
    global _paths
    if _paths is None:
        _paths = get_paths()
    return _paths


def reset_paths(repo_root: Path | None = None) -> Paths:
    """Reset cached paths. Useful for testing or when repo root changes."""
    global _paths
    _paths = get_paths(repo_root)
    return _paths


# ---------------------------------------------------------------------------
# Low-level JSONL I/O
# ---------------------------------------------------------------------------


def ensure_state_dir() -> None:
    p = _get_paths()
    p.state_dir.mkdir(parents=True, exist_ok=True)
    for f in (
        p.criteria_file,
        p.verifications_file,
        p.approvals_file,
        p.requirements_file,
        p.specs_file,
    ):
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


def append_jsonl(path: Path, entry: dict) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")


def next_id(path: Path, prefix: str) -> str:
    """Generate a UUID-based ID with the given prefix.

    Format: ``prefix-<12 hex chars>`` (e.g. ``req-a1b2c3d4e5f6``).
    Branch-safe: independent branches won't collide.
    """
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Legacy .md parsing (migration-only — not used in normal operation)
# ---------------------------------------------------------------------------


def parse_requirement_body(path: Path) -> dict[int, str]:
    """Parse requirement .md body, returning {req_no: text}."""
    text = path.read_text(encoding="utf-8-sig")
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            text = text[end + 3 :]
    result = {}
    for m in re.finditer(r"^(\d+)\.\s+(.+)$", text, re.MULTILINE):
        result[int(m.group(1))] = m.group(2).strip()
    return result


def parse_spec_frontmatter(path: Path) -> dict[str, Any] | None:
    """Parse spec .md frontmatter, returning {requirement, commits, files}."""
    import yaml

    text = path.read_text(encoding="utf-8-sig")
    match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return None
    try:
        data = yaml.safe_load(match.group(1))
        if not data or not isinstance(data, dict):
            return None
        if "requirement" not in data:
            return None
        body = text[match.end() :].strip()
        return {
            "requirement": str(data.get("requirement", "")),
            "commits": data.get("commits", []) or [],
            "files": data.get("files", []) or [],
            "body": body,
        }
    except yaml.YAMLError:
        return None


# ---------------------------------------------------------------------------
# DuckDB query layer
# ---------------------------------------------------------------------------


def _duckdb_path(p: Path) -> str:
    """Convert Path to forward-slash string for DuckDB."""
    return str(p).replace("\\", "/")


def _load_table(con: duckdb.DuckDBPyConnection, table_name: str, path: Path) -> None:
    """Load a JSONL file into an in-memory DuckDB table with explicit schema.

    Empty files (0 bytes) produce a correctly-typed empty table.
    """
    dp = _duckdb_path(path)
    cols = _schema.duckdb_columns(table_name)

    if not path.exists() or path.stat().st_size == 0:
        con.execute(_schema.duckdb_ddl(table_name))
    else:
        col_spec = ", ".join(f"'{k}': '{v}'" for k, v in cols.items())
        con.execute(
            f"CREATE TABLE {table_name} AS "
            f"SELECT * FROM read_json('{dp}', "
            f"format='newline_delimited', columns={{{col_spec}}})"
        )


def _save_table(con: duckdb.DuckDBPyConnection, table_name: str, path: Path) -> None:
    """Write a DuckDB table back to JSONL, sorted deterministically.

    Uses temp file + atomic rename for crash safety.
    """
    sort_cols = _schema.SORT_KEYS.get(table_name, ["id"])
    order_clause = ", ".join(sort_cols)

    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), suffix=".jsonl.tmp", prefix=f".{table_name}_"
    )
    os.close(fd)
    tmp_dp = _duckdb_path(Path(tmp))

    try:
        con.execute(
            f"COPY (SELECT * FROM {table_name} ORDER BY {order_clause}) "
            f"TO '{tmp_dp}' (FORMAT JSON, ARRAY false)"
        )
        os.replace(tmp, str(path))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _file_hash(path: Path) -> str:
    """Return hex SHA-256 of file contents (empty string for missing/empty)."""
    if not path.exists() or path.stat().st_size == 0:
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _append_and_save(table_name: str, path: Path, entry: dict) -> None:
    """Append *entry* to the JSONL file using the rewrite strategy.

    Steps: load existing data → INSERT new row → COPY sorted to file.
    """
    con = duckdb.connect(":memory:")
    try:
        _load_table(con, table_name, path)
        cols = list(entry.keys())
        placeholders = ", ".join("?" for _ in cols)
        col_names = ", ".join(cols)
        con.execute(
            f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders})",
            list(entry.values()),
        )
        _save_table(con, table_name, path)
    finally:
        con.close()


def query_full_status(req_file: str | None = None) -> list[dict]:
    """
    Query full status by joining all 5 JSONL files via DuckDB.

    Returns list of dicts with keys:
        requirement, req_no, req_text, criteria_id, criterion, req_text_hash,
        v_id, v_status, v_detail, v_limitation, verified_at,
        a_decision, a_comment,
        is_stale
    """
    p = _get_paths()
    ensure_state_dir()

    # Load requirement texts from requirements.jsonl
    req_texts: dict[tuple[str, int], str] = {}
    for r in read_jsonl(p.requirements_file):
        req_texts[(r["file"], r["req_no"])] = r["text"]

    con = duckdb.connect(":memory:")
    try:
        _load_table(con, "acceptance_criteria", p.criteria_file)
        _load_table(con, "verifications", p.verifications_file)
        _load_table(con, "approvals", p.approvals_file)

        sql = """
        WITH latest_v AS (
            SELECT *
            FROM verifications
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY criteria_id ORDER BY verified_at DESC, id DESC
            ) = 1
        ),
        latest_a AS (
            SELECT *
            FROM approvals
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY verification_id ORDER BY decided_at DESC, id DESC
            ) = 1
        )
        SELECT
            c.id as criteria_id, c.requirement, c.req_no,
            c.criterion, c.req_text_hash,
            v.id as v_id, v.status as v_status,
            v.detail as v_detail, v.limitation as v_limitation,
            v.verified_at,
            a.decision as a_decision, a.comment as a_comment
        FROM acceptance_criteria c
        LEFT JOIN latest_v v ON v.criteria_id = c.id
        LEFT JOIN latest_a a ON a.verification_id = v.id
        ORDER BY c.requirement, c.req_no
        """

        rows = con.execute(sql).fetchall()
        columns = [
            "criteria_id",
            "requirement",
            "req_no",
            "criterion",
            "req_text_hash",
            "v_id",
            "v_status",
            "v_detail",
            "v_limitation",
            "verified_at",
            "a_decision",
            "a_comment",
        ]

        result = []
        for row in rows:
            d = dict(zip(columns, row))
            # Add requirement text
            key = (d["requirement"], d["req_no"])
            d["req_text"] = req_texts.get(key, "")
            # Compute STALE
            if d["req_text"] and d["req_text_hash"]:
                d["is_stale"] = hash_text(d["req_text"]) != d["req_text_hash"]
            else:
                d["is_stale"] = False
            result.append(d)

        if req_file:
            req_name = req_file.replace(".md", "")
            result = [r for r in result if r["requirement"] == req_name]

        return result
    finally:
        con.close()


def compute_summary(rows: list[dict]) -> dict[str, int]:
    """Compute summary counts from query_full_status results."""
    summary = {
        "total": len(rows),
        "approved": 0,
        "pending": 0,
        "failed": 0,
        "regression": 0,
        "stale": 0,
        "no_verification": 0,
    }
    for r in rows:
        if r.get("is_stale"):
            summary["stale"] += 1
        if r.get("v_status") is None:
            summary["no_verification"] += 1
        elif r["v_status"] in ("failed",):
            summary["failed"] += 1
        elif r["v_status"] in ("regression",):
            summary["regression"] += 1
        elif r.get("a_decision") == "approved":
            summary["approved"] += 1
        else:
            summary["pending"] += 1
    return summary


def query_specs() -> list[dict]:
    """Query all specs from specs.jsonl."""
    p = _get_paths()
    ensure_state_dir()
    return read_jsonl(p.specs_file)


def build_dependency_graph() -> dict[str, Any]:
    """
    Build dependency graph for Mermaid visualization.

    Returns {
        "requirements": {file: [{req_no, text, criteria: [...]}]},
        "specs": [{requirement, commits, files, title}],
        "links": [(from_id, to_id, label)]
    }
    """
    p = _get_paths()
    status_rows = query_full_status()
    specs = query_specs()

    # Group by requirement file
    requirements: dict[str, dict[int, dict]] = {}
    for r in status_rows:
        file = r["requirement"]
        req_no = r["req_no"]
        if file not in requirements:
            requirements[file] = {}
        if req_no not in requirements[file]:
            requirements[file][req_no] = {
                "req_no": req_no,
                "text": r.get("req_text", ""),
                "criteria": [],
            }
        requirements[file][req_no]["criteria"].append(r)

    links: list[tuple[str, str, str]] = []

    # Requirement -> Spec links
    for spec in specs:
        req_file = spec["requirement"]
        links.append((f"REQ_{req_file}", f"SPEC_{spec['id']}", "implements"))
        for f in spec.get("files", []):
            links.append((f"SPEC_{spec['id']}", f"FILE_{f}", "modifies"))

    # Requirement -> Criteria -> Verification -> Approval links
    for file, reqs in requirements.items():
        for req_no, req_data in reqs.items():
            for crit in req_data["criteria"]:
                cid = crit["criteria_id"]
                links.append(
                    (f"REQ_{file}_{req_no}", f"AC_{cid}", "criteria")
                )
                if crit.get("v_id"):
                    links.append(
                        (f"AC_{cid}", f"V_{crit['v_id']}", "verified")
                    )

    return {
        "requirements": {
            f: sorted(reqs.values(), key=lambda x: x["req_no"])
            for f, reqs in sorted(requirements.items())
        },
        "specs": specs,
        "links": links,
    }


def _mermaid_escape(text: str) -> str:
    """Escape text for use in Mermaid node labels."""
    # Remove/replace characters that break Mermaid syntax
    text = text.replace('"', "'")
    text = text.replace("[", "(")
    text = text.replace("]", ")")
    text = text.replace("{", "(")
    text = text.replace("}", ")")
    text = text.replace("<", "‹")
    text = text.replace(">", "›")
    text = text.replace("|", "¦")
    text = text.replace("&", "+")
    return text


def generate_mermaid(graph: dict | None = None) -> str:
    """Generate Mermaid diagram string from dependency graph."""
    if graph is None:
        graph = build_dependency_graph()

    lines = ["graph LR"]

    # Requirement nodes
    for file, reqs in graph["requirements"].items():
        lines.append(f'  REQ_{file}["{file}.md"]')
        for req in reqs:
            req_no = req["req_no"]
            text = req["text"][:30] + ("..." if len(req["text"]) > 30 else "")
            text = _mermaid_escape(text)
            lines.append(f'  REQ_{file}_{req_no}["{req_no}. {text}"]')
            lines.append(f"  REQ_{file} --> REQ_{file}_{req_no}")

            for crit in req["criteria"]:
                cid = crit["criteria_id"]
                status = crit.get("v_status", "none")
                decision = crit.get("a_decision", "")
                stale = crit.get("is_stale", False)

                if decision == "approved":
                    style = ":::approved"
                elif status in ("failed", "regression"):
                    style = ":::failed"
                elif stale:
                    style = ":::stale"
                else:
                    style = ":::pending"

                crit_text = crit["criterion"][:25]
                crit_text = _mermaid_escape(crit_text)
                lines.append(f'  {cid}["{cid}: {crit_text}"]{style}')
                lines.append(f"  REQ_{file}_{req_no} --> {cid}")

    # Spec nodes
    for spec in graph["specs"]:
        sid = spec["id"]
        req = spec["requirement"]
        lines.append(f'  {sid}["{sid}"]')
        lines.append(f"  REQ_{req} -.->|spec| {sid}")

    # Style classes
    lines.append("  classDef approved fill:#2d6a4f,stroke:#40916c,color:#fff")
    lines.append("  classDef failed fill:#9d0208,stroke:#d00000,color:#fff")
    lines.append("  classDef stale fill:#e76f51,stroke:#f4a261,color:#fff")
    lines.append("  classDef pending fill:#7b6d00,stroke:#ffd60a,color:#fff")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Requirement operations
# ---------------------------------------------------------------------------


def list_requirements(file: str | None = None) -> list[dict]:
    """List all requirements from requirements.jsonl."""
    p = _get_paths()
    ensure_state_dir()
    reqs = read_jsonl(p.requirements_file)

    if file:
        file = file.replace(".md", "")
        reqs = [r for r in reqs if r["file"] == file]

    return sorted(reqs, key=lambda r: (r["file"], r["req_no"]))


def add_requirement(file: str, text: str, by: str = "user") -> dict:
    """Add a new requirement to requirements.jsonl."""
    p = _get_paths()
    ensure_state_dir()
    file = file.replace(".md", "")

    # Determine next req_no (max existing + 10)
    existing = [
        r for r in read_jsonl(p.requirements_file) if r["file"] == file
    ]
    all_nos = {r["req_no"] for r in existing}
    max_no = max(all_nos) if all_nos else 0
    new_no = max_no + 10

    new_id = next_id(p.requirements_file, "req")
    entry = {
        "id": new_id,
        "file": file,
        "req_no": new_no,
        "text": text,
        "created_at": now_iso(),
        "created_by": by,
    }
    _append_and_save("requirements", p.requirements_file, entry)
    return entry


def list_requirement_files() -> list[str]:
    """List unique requirement file names from requirements.jsonl."""
    p = _get_paths()
    files: set[str] = set()
    for r in read_jsonl(p.requirements_file):
        files.add(r["file"])
    return sorted(files)


# ---------------------------------------------------------------------------
# Spec operations
# ---------------------------------------------------------------------------


def add_spec(
    requirement: str,
    title: str,
    body: str,
    commits: list[str] | None = None,
    files: list[str] | None = None,
    by: str = "agent",
) -> dict:
    """Add a new spec to specs.jsonl."""
    p = _get_paths()
    ensure_state_dir()

    new_id = next_id(p.specs_file, "spec")
    entry = {
        "id": new_id,
        "requirement": requirement.replace(".md", ""),
        "commits": commits or [],
        "files": files or [],
        "title": title,
        "body": body,
        "created_at": now_iso(),
        "created_by": by,
    }
    _append_and_save("specs", p.specs_file, entry)
    return entry


# ---------------------------------------------------------------------------
# Criteria / Verification / Approval operations
# ---------------------------------------------------------------------------


def add_criteria(
    req_file: str, req_no: int, criterion: str, by: str = "agent"
) -> dict:
    """Add acceptance criterion to acceptance_criteria.jsonl."""
    p = _get_paths()
    ensure_state_dir()
    req_file = req_file.replace(".md", "")

    # Get requirement text for hash
    reqs = list_requirements(req_file)
    req_text = ""
    for r in reqs:
        if r["req_no"] == req_no:
            req_text = r["text"]
            break
    if not req_text:
        raise ValueError(f"Requirement {req_file}#{req_no} not found")

    new_id = next_id(p.criteria_file, "ac")
    entry = {
        "id": new_id,
        "requirement": req_file,
        "req_no": req_no,
        "criterion": criterion,
        "req_text_hash": hash_text(req_text),
        "created_at": now_iso(),
        "created_by": by,
    }
    _append_and_save("acceptance_criteria", p.criteria_file, entry)
    return entry


def verify(
    criteria_id: str,
    status: str,
    detail: str = "",
    limitation: str = "",
    by: str = "agent",
) -> dict:
    """Record verification result."""
    p = _get_paths()
    ensure_state_dir()

    if status not in ("passed", "failed", "conditional"):
        raise ValueError(f"Invalid status '{status}'. Use: passed, failed, conditional")

    criteria = read_jsonl(p.criteria_file)
    target = next((c for c in criteria if c["id"] == criteria_id), None)
    if target is None:
        raise ValueError(f"Criteria {criteria_id} not found")

    new_id = next_id(p.verifications_file, "v")
    entry = {
        "id": new_id,
        "criteria_id": criteria_id,
        "requirement": target["requirement"],
        "req_no": target["req_no"],
        "status": status,
        "detail": detail,
        "limitation": limitation,
        "verified_at": now_iso(),
        "verified_by": by,
    }
    _append_and_save("verifications", p.verifications_file, entry)
    return entry


def approve(
    verification_id: str, decision: str, comment: str = ""
) -> dict:
    """Approve or reject a verification."""
    p = _get_paths()
    ensure_state_dir()

    if decision not in ("approved", "rejected"):
        raise ValueError(
            f"Invalid decision '{decision}'. Use: approved, rejected"
        )

    verifications = read_jsonl(p.verifications_file)
    target = next(
        (v for v in verifications if v["id"] == verification_id), None
    )
    if target is None:
        raise ValueError(f"Verification {verification_id} not found")

    new_id = next_id(p.approvals_file, "a")
    entry = {
        "id": new_id,
        "verification_id": verification_id,
        "decision": decision,
        "comment": comment,
        "decided_at": now_iso(),
    }
    _append_and_save("approvals", p.approvals_file, entry)
    return entry


def regress(
    req_file: str,
    req_no: int,
    detail: str,
    criteria_id: str | None = None,
    by: str = "agent",
) -> dict:
    """Record regression as a failed verification."""
    p = _get_paths()
    ensure_state_dir()
    req_file = req_file.replace(".md", "")

    criteria = read_jsonl(p.criteria_file)
    matching = [
        c
        for c in criteria
        if c["requirement"] == req_file and c["req_no"] == req_no
    ]

    target_cid = ""
    if criteria_id:
        target = next((c for c in criteria if c["id"] == criteria_id), None)
        if target is None:
            raise ValueError(f"Criteria {criteria_id} not found")
        if target["requirement"] != req_file or target["req_no"] != req_no:
            raise ValueError(
                f"Criteria {criteria_id} belongs to "
                f"{target['requirement']}#{target['req_no']}, "
                f"not {req_file}#{req_no}"
            )
        target_cid = criteria_id
    elif matching:
        if len(matching) > 1:
            ids = ", ".join(c["id"] for c in matching)
            raise ValueError(
                f"Multiple criteria for {req_file}#{req_no}: {ids}. "
                f"Specify one with criteria_id."
            )
        target_cid = matching[0]["id"]
    else:
        # Auto-create criterion
        reqs = list_requirements(req_file)
        req_text = ""
        for r in reqs:
            if r["req_no"] == req_no:
                req_text = r["text"]
                break
        req_text = req_text or f"Requirement {req_no}"

        cid = next_id(p.criteria_file, "ac")
        c_entry = {
            "id": cid,
            "requirement": req_file,
            "req_no": req_no,
            "criterion": req_text,
            "req_text_hash": hash_text(req_text),
            "created_at": now_iso(),
            "created_by": "auto",
        }
        _append_and_save("acceptance_criteria", p.criteria_file, c_entry)
        target_cid = cid

    new_id = next_id(p.verifications_file, "v")
    entry = {
        "id": new_id,
        "criteria_id": target_cid,
        "requirement": req_file,
        "req_no": req_no,
        "status": "regression",
        "detail": detail,
        "limitation": "",
        "verified_at": now_iso(),
        "verified_by": by,
    }
    _append_and_save("verifications", p.verifications_file, entry)
    return entry


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def migrate_requirements_md() -> dict[str, int]:
    """Migrate requirements from .md files to requirements.jsonl."""
    p = _get_paths()
    ensure_state_dir()

    existing = {(r["file"], r["req_no"]) for r in read_jsonl(p.requirements_file)}
    count = 0

    if not p.requirements_dir.exists():
        return {"migrated": 0}

    for md_file in sorted(p.requirements_dir.glob("*.md")):
        body = parse_requirement_body(md_file)
        for req_no, text in sorted(body.items()):
            key = (md_file.stem, req_no)
            if key not in existing:
                new_id = next_id(p.requirements_file, "req")
                entry = {
                    "id": new_id,
                    "file": md_file.stem,
                    "req_no": req_no,
                    "text": text,
                    "created_at": now_iso(),
                    "created_by": "migration",
                }
                _append_and_save("requirements", p.requirements_file, entry)
                existing.add(key)
                count += 1

    return {"migrated": count}


def migrate_specs_md() -> dict[str, int]:
    """Migrate legacy spec markdown files into state storage."""
    p = _get_paths()
    ensure_state_dir()

    existing = {s.get("requirement") for s in read_jsonl(p.specs_file)}
    count = 0

    if not p.specs_dir.exists():
        return {"migrated": 0}

    for md_file in sorted(p.specs_dir.glob("*.md")):
        fm = parse_spec_frontmatter(md_file)
        if not fm:
            continue
        if fm["requirement"] in existing:
            continue

        new_id = next_id(p.specs_file, "spec")
        entry = {
            "id": new_id,
            "requirement": fm["requirement"],
            "commits": fm["commits"],
            "files": fm["files"],
            "title": md_file.stem,
            "body": fm["body"],
            "created_at": now_iso(),
            "created_by": "migration",
        }
        _append_and_save("specs", p.specs_file, entry)
        existing.add(fm["requirement"])
        count += 1

    return {"migrated": count}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_state() -> dict[str, list[str]]:
    """Validate all JSONL state files against the canonical schema.

    Returns ``{filename: [error_messages]}`` — empty dict means all valid.
    """
    p = _get_paths()
    ensure_state_dir()

    file_map: dict[str, tuple[str, Path]] = {
        "requirements.jsonl": ("requirements", p.requirements_file),
        "acceptance_criteria.jsonl": ("acceptance_criteria", p.criteria_file),
        "verifications.jsonl": ("verifications", p.verifications_file),
        "approvals.jsonl": ("approvals", p.approvals_file),
        "specs.jsonl": ("specs", p.specs_file),
    }

    all_errors: dict[str, list[str]] = {}

    # Per-file validation
    ids_by_table: dict[str, set[str]] = {}
    for filename, (table_name, path) in file_map.items():
        errors: list[str] = []
        schema_cols = _schema.TABLE_SCHEMAS[table_name]
        required = _schema.required_fields(table_name)
        entries = read_jsonl(path)
        ids_seen: set[str] = set()

        # Composite key tracking
        composite_keys_seen: dict[tuple[str, ...], set[tuple]] = {}
        for key_fields in _schema.UNIQUE_KEYS.get(table_name, []):
            composite_keys_seen[key_fields] = set()

        for line_no, entry in enumerate(entries, 1):
            # Field presence + type
            for col in schema_cols:
                if col.name not in entry:
                    if col.required:
                        errors.append(f"Line {line_no}: missing required field '{col.name}'")
                else:
                    expected = _schema.python_type_for(col.logical_type)
                    if not isinstance(entry[col.name], expected):
                        errors.append(
                            f"Line {line_no}: '{col.name}' expected "
                            f"{col.logical_type}, got {type(entry[col.name]).__name__}"
                        )

            # ID uniqueness
            eid = entry.get("id", "")
            if eid in ids_seen:
                errors.append(f"Line {line_no}: duplicate ID '{eid}'")
            ids_seen.add(eid)

            # Composite key uniqueness
            for key_fields, seen in composite_keys_seen.items():
                key_val = tuple(entry.get(k) for k in key_fields)
                if all(v is not None for v in key_val):
                    if key_val in seen:
                        errors.append(
                            f"Line {line_no}: duplicate key {dict(zip(key_fields, key_val))}"
                        )
                    seen.add(key_val)

            # Enum checks
            if table_name == "verifications":
                st = entry.get("status", "")
                if st and st not in _schema.VALID_STATUSES:
                    errors.append(
                        f"Line {line_no}: invalid status '{st}' "
                        f"(expected: {', '.join(sorted(_schema.VALID_STATUSES))})"
                    )
            if table_name == "approvals":
                dec = entry.get("decision", "")
                if dec and dec not in _schema.VALID_DECISIONS:
                    errors.append(
                        f"Line {line_no}: invalid decision '{dec}' "
                        f"(expected: {', '.join(sorted(_schema.VALID_DECISIONS))})"
                    )

        ids_by_table[table_name] = ids_seen
        if errors:
            all_errors[filename] = errors

    # Cross-file referential integrity
    ref_errors: list[str] = []
    criteria_ids = ids_by_table.get("acceptance_criteria", set())
    verification_ids = ids_by_table.get("verifications", set())

    for entry in read_jsonl(p.verifications_file):
        cid = entry.get("criteria_id", "")
        if cid and cid not in criteria_ids:
            ref_errors.append(
                f"verifications: '{entry.get('id')}' references unknown criteria_id '{cid}'"
            )

    for entry in read_jsonl(p.approvals_file):
        vid = entry.get("verification_id", "")
        if vid and vid not in verification_ids:
            ref_errors.append(
                f"approvals: '{entry.get('id')}' references unknown verification_id '{vid}'"
            )

    if ref_errors:
        all_errors["_referential_integrity"] = ref_errors

    return all_errors
