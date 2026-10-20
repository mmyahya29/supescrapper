from __future__ import annotations

import csv
import io
import json
import sqlite3
from pathlib import Path

from .exceptions import ExportError


def _resolve_path(dest: str | Path) -> Path:
    p = Path(dest)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def export_json(results, dest: str | Path, *, indent: int | None = 2) -> Path:
    from .models import ScrapedResultSet
    p = _resolve_path(dest)
    data = results.to_list() if isinstance(results, ScrapedResultSet) else [r.to_dict() for r in results]
    try:
        p.write_text(json.dumps(data, indent=indent, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        raise ExportError(f"Failed to write JSON: {exc}") from exc
    return p


def export_csv(results, dest: str | Path, *, delimiter: str = ",") -> Path:
    from .models import ScrapedResultSet
    p = _resolve_path(dest)
    rows = results.to_list() if isinstance(results, ScrapedResultSet) else [r.to_dict() for r in results]
    if not rows:
        p.write_text("", encoding="utf-8")
        return p

    all_keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row:
            if k not in seen:
                all_keys.append(k)
                seen.add(k)

    try:
        with p.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_keys, delimiter=delimiter, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    except Exception as exc:
        raise ExportError(f"Failed to write CSV: {exc}") from exc
    return p


def export_sqlite(results, dest: str | Path, *, table: str = "results", unique_keys: list[str] | None = None) -> Path:
    from .models import ScrapedResultSet
    p = _resolve_path(dest)
    rows = results.to_list() if isinstance(results, ScrapedResultSet) else [r.to_dict() for r in results]
    if not rows:
        return p

    all_keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row:
            if k not in seen:
                all_keys.append(k)
                seen.add(k)

    try:
        conn = sqlite3.connect(str(p))
        cur = conn.cursor()
        col_defs = ", ".join(f'"{k}" TEXT' for k in all_keys)
        cur.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({col_defs})')

        if unique_keys:
            valid_unique = [k for k in unique_keys if k in all_keys]
            if valid_unique:
                cols_for_idx = ", ".join(f'"{k}"' for k in valid_unique)
                idx_name = f"uq_{table}_{'_'.join(valid_unique)}"
                cur.execute(f'CREATE UNIQUE INDEX IF NOT EXISTS "{idx_name}" ON "{table}" ({cols_for_idx})')
                placeholders = ", ".join("?" for _ in all_keys)
                insert_sql = f'INSERT OR REPLACE INTO "{table}" ({", ".join(f"{k!r}" for k in all_keys)}) VALUES ({placeholders})'
            else:
                placeholders = ", ".join("?" for _ in all_keys)
                insert_sql = f'INSERT INTO "{table}" ({", ".join(f"{k!r}" for k in all_keys)}) VALUES ({placeholders})'
        else:
            placeholders = ", ".join("?" for _ in all_keys)
            insert_sql = f'INSERT INTO "{table}" ({", ".join(f"{k!r}" for k in all_keys)}) VALUES ({placeholders})'

        for row in rows:
            values = [row.get(k, "") for k in all_keys]
            cur.execute(insert_sql, values)
        conn.commit()
        conn.close()
    except Exception as exc:
        raise ExportError(f"Failed to write SQLite: {exc}") from exc
    return p
