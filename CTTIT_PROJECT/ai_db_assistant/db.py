from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


class UnsafeSQLError(ValueError):
    """Raised when the SQL query is not allowed (non read-only / multi-statement / dangerous)."""


_DISALLOWED_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE", "VACUUM",
    "ATTACH", "DETACH", "PRAGMA", "BEGIN", "COMMIT", "ROLLBACK", "TRUNCATE",
)
_DISALLOWED_RE = re.compile(r"\b(" + "|".join(_DISALLOWED_KEYWORDS) + r")\b", re.IGNORECASE)


def _strip_sql_comments(sql: str) -> str:
    # Remove -- line comments
    sql = re.sub(r"--[^\n]*", "", sql)
    # Remove /* block comments */
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    return sql


def _mask_sql_string_literals(sql: str) -> str:
    """
    Replace content of string literals so keyword checks don't trigger inside strings.
    Handles single quotes; keeps length/structure roughly stable.
    """
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            out.append("'")
            i += 1
            # SQL single-quote escaping is '' (two single quotes)
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        out.append("''")
                        i += 2
                        continue
                    out.append("'")
                    i += 1
                    break
                out.append("x")
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _normalize_sql(sql: str) -> str:
    sql = sql.strip()
    sql = _strip_sql_comments(sql)
    sql = sql.strip()
    # Remove a single trailing semicolon (common)
    sql = re.sub(r";\s*$", "", sql)
    return sql.strip()


def _ensure_single_statement(sql: str) -> None:
    # After removing trailing semicolon, any remaining semicolon implies multiple statements
    if ";" in sql:
        raise UnsafeSQLError("Multiple SQL statements are not allowed.")


def _ensure_readonly(sql: str) -> None:
    if not sql:
        raise UnsafeSQLError("Empty SQL is not allowed.")

    masked = _mask_sql_string_literals(sql)
    if _DISALLOWED_RE.search(masked):
        raise UnsafeSQLError("Only read-only queries are allowed (SELECT/WITH).")

    first_token = re.split(r"\s+", masked.strip(), maxsplit=1)[0].upper()
    if first_token not in ("SELECT", "WITH"):
        raise UnsafeSQLError("Only SELECT/WITH queries are allowed.")


def _apply_row_limit(sql: str, limit: int) -> str:
    """
    Enforce a hard limit to avoid huge outputs.
    If SQL already contains LIMIT (best-effort), keep it.
    Otherwise wrap with SELECT * FROM ( ... ) LIMIT N.
    """
    masked = _mask_sql_string_literals(sql)
    if re.search(r"\bLIMIT\b", masked, flags=re.IGNORECASE):
        return sql
    return f"SELECT * FROM ({sql}) LIMIT {int(limit)}"


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    # Read-only connection using SQLite URI
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def run_sql_query(db_path: str, query: str, max_rows: int = 200) -> dict[str, Any]:
    """
    Execute a read-only SQL query against SQLite and return a JSON-serializable result.

    Security:
    - Only allows SELECT/WITH
    - Blocks common write/DDL keywords
    - Blocks multi-statement queries
    - Enforces max row limit

    Returns:
      {
        "ok": bool,
        "query": str,
        "columns": [str, ...],
        "rows": [[...], ...],
        "row_count": int,
        "error": str | null
      }
    """
    p = Path(db_path)
    if not p.exists():
        return {
            "ok": False,
            "query": query,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "error": f"Database file not found: {p}",
        }

    normalized = _normalize_sql(query)
    try:
        _ensure_single_statement(normalized)
        _ensure_readonly(normalized)
        limited = _apply_row_limit(normalized, max_rows)

        conn = _connect_readonly(p)
        try:
            cur = conn.execute(limited)
            fetched = cur.fetchall()
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = [[row[col] for col in columns] for row in fetched]
            return {
                "ok": True,
                "query": limited,
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "error": None,
            }
        finally:
            conn.close()

    except UnsafeSQLError as e:
        return {
            "ok": False,
            "query": normalized,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "error": f"Unsafe SQL: {e}",
        }
    except sqlite3.Error as e:
        return {
            "ok": False,
            "query": normalized,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "error": f"SQLite error: {e}",
        }


def tool_result_to_json(result: dict[str, Any]) -> str:
    """Stable JSON for sending back to the model as tool output."""
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))