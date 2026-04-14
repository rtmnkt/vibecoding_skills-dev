"""
Canonical schema definitions for req-driven-dev state files.

This module is the single source of truth for all JSONL table schemas.
It has ZERO external dependencies — safe to import from any tool.

Schema evolution policy:
  - Additive only: new fields must be optional with a default value.
  - Never delete or rename existing fields.
  - Increment SCHEMA_VERSION when adding fields or migrations.
  - Git merge conflicts on this file are the primary defense against
    concurrent schema changes across branches.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------

SCHEMA_VERSION: int = 1

# ---------------------------------------------------------------------------
# Column definition
# ---------------------------------------------------------------------------


class Column:
    """Immutable column definition."""

    __slots__ = ("name", "logical_type", "required", "default")

    def __init__(
        self,
        name: str,
        logical_type: str,
        *,
        required: bool = True,
        default: Any = None,
    ) -> None:
        self.name = name
        self.logical_type = logical_type
        self.required = required
        self.default = default

    def __repr__(self) -> str:
        return f"Column({self.name!r}, {self.logical_type!r})"


# ---------------------------------------------------------------------------
# Logical type → DuckDB type mapping
# ---------------------------------------------------------------------------

_DUCKDB_TYPE_MAP: dict[str, str] = {
    "str": "VARCHAR",
    "int": "INTEGER",
    "list[str]": "VARCHAR[]",
}

# Logical type → Python type(s) for validation
_PYTHON_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "str": str,
    "int": (int,),  # tuple so isinstance() works uniformly
    "list[str]": list,
}

# ---------------------------------------------------------------------------
# Table schemas
# ---------------------------------------------------------------------------

TABLE_SCHEMAS: dict[str, list[Column]] = {
    "requirements": [
        Column("id", "str"),
        Column("file", "str"),
        Column("req_no", "int"),
        Column("text", "str"),
        Column("created_at", "str"),
        Column("created_by", "str"),
    ],
    "acceptance_criteria": [
        Column("id", "str"),
        Column("requirement", "str"),
        Column("req_no", "int"),
        Column("criterion", "str"),
        Column("req_text_hash", "str"),
        Column("created_at", "str"),
        Column("created_by", "str"),
    ],
    "verifications": [
        Column("id", "str"),
        Column("criteria_id", "str"),
        Column("requirement", "str"),
        Column("req_no", "int"),
        Column("status", "str"),
        Column("detail", "str", required=False, default=""),
        Column("limitation", "str", required=False, default=""),
        Column("verified_at", "str"),
        Column("verified_by", "str"),
    ],
    "approvals": [
        Column("id", "str"),
        Column("verification_id", "str"),
        Column("decision", "str"),
        Column("comment", "str", required=False, default=""),
        Column("decided_at", "str"),
    ],
    "specs": [
        Column("id", "str"),
        Column("requirement", "str"),
        Column("commits", "list[str]", required=False, default=[]),
        Column("files", "list[str]", required=False, default=[]),
        Column("title", "str"),
        Column("body", "str"),
        Column("created_at", "str"),
        Column("created_by", "str"),
    ],
}

# ---------------------------------------------------------------------------
# Sort keys (deterministic JSONL output for clean git diffs)
# ---------------------------------------------------------------------------

SORT_KEYS: dict[str, list[str]] = {
    "requirements": ["file", "req_no"],
    "acceptance_criteria": ["requirement", "req_no", "id"],
    "verifications": ["requirement", "req_no", "criteria_id", "verified_at", "id"],
    "approvals": ["verification_id", "decided_at", "id"],
    "specs": ["requirement", "id"],
}

# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------

VALID_STATUSES: set[str] = {"passed", "failed", "conditional", "regression"}
VALID_DECISIONS: set[str] = {"approved", "rejected"}

UNIQUE_KEYS: dict[str, list[tuple[str, ...]]] = {
    "requirements": [("file", "req_no")],
}

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def duckdb_columns(table_name: str) -> dict[str, str]:
    """Return ``{col_name: duckdb_type}`` for ``read_json(columns=...)``."""
    schema = TABLE_SCHEMAS[table_name]
    return {c.name: _DUCKDB_TYPE_MAP[c.logical_type] for c in schema}


def duckdb_ddl(table_name: str) -> str:
    """Return a ``CREATE TABLE`` DDL statement for *table_name*."""
    schema = TABLE_SCHEMAS[table_name]
    col_defs = ", ".join(
        f"{c.name} {_DUCKDB_TYPE_MAP[c.logical_type]}" for c in schema
    )
    return f"CREATE TABLE {table_name} ({col_defs})"


def python_type_for(logical_type: str) -> type | tuple[type, ...]:
    """Return the Python type(s) corresponding to *logical_type*."""
    return _PYTHON_TYPE_MAP[logical_type]


def required_fields(table_name: str) -> list[str]:
    """Return names of required (non-optional) fields."""
    return [c.name for c in TABLE_SCHEMAS[table_name] if c.required]


def all_fields(table_name: str) -> list[str]:
    """Return all field names for *table_name*."""
    return [c.name for c in TABLE_SCHEMAS[table_name]]


def defaults(table_name: str) -> dict[str, Any]:
    """Return ``{field: default}`` for optional fields."""
    return {
        c.name: c.default
        for c in TABLE_SCHEMAS[table_name]
        if not c.required and c.default is not None
    }


# ---------------------------------------------------------------------------
# Migrations (version → list of SQL statements to run on loaded tables)
# ---------------------------------------------------------------------------

MIGRATIONS: dict[int, list[str]] = {
    # Example for future use:
    # 2: ["ALTER TABLE verifications ADD COLUMN reviewer VARCHAR DEFAULT ''"],
}
