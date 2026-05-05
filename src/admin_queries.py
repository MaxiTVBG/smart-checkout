from __future__ import annotations

import csv
import datetime as _dt
import io
import re
import sqlite3
from pathlib import Path
from typing import Any


DEFAULT_DB_PATH = Path("data/inventory.db")
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def resolve_db_path(db_path: str | None = None, config_path: str = "config.yaml") -> Path:
    if db_path:
        return Path(db_path).expanduser().resolve()

    config_file = Path(config_path)
    if config_file.exists():
        try:
            import yaml

            config = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
            configured = config.get("paths", {}).get("db_path")
            if configured:
                return Path(configured).expanduser().resolve()
        except Exception:
            pass

    return DEFAULT_DB_PATH.resolve()


def connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA query_only=ON")
    return conn


def connect_writable(db_path: str | Path) -> sqlite3.Connection:
    """Writable connection for admin actions (manual add/remove, code toggle)."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    # Enforce WAL mode for better concurrency
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def trigger_webhook(event: str, payload: dict[str, Any]) -> None:
    config_file = Path("config.yaml")
    if not config_file.exists():
        return
    try:
        import yaml
        import requests
        config = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
        webhook_url = config.get("webhooks", {}).get("url")
        if not webhook_url:
            return
        
        data = {
            "event": event,
            "data": payload,
            "timestamp": _dt.datetime.now().isoformat()
        }
        # Send webhook asynchronously or in background
        # For simplicity, we just send it with a short timeout here.
        requests.post(webhook_url, json=data, timeout=2.0)
    except Exception as e:
        print(f"Failed to send webhook: {e}")

def log_admin_action(conn: sqlite3.Connection, action_type: str, entity_id: str, user: dict[str, str], details: str = "") -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            user_method TEXT NOT NULL,
            user_identifier TEXT NOT NULL,
            user_role TEXT NOT NULL,
            action_type TEXT NOT NULL,
            entity_id TEXT,
            details TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO admin_audit_logs 
        (timestamp, user_method, user_identifier, user_role, action_type, entity_id, details)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _dt.datetime.now().isoformat(),
            user.get("method", "unknown"),
            user.get("identifier", "unknown"),
            user.get("role", "unknown"),
            action_type,
            entity_id,
            details
        )
    )


def manual_add_item(conn: sqlite3.Connection, uid: str, item_class: str, user: dict[str, str]) -> str:
    """Manually mark an item as ADDED (in stock). Returns status message."""
    uid = uid.strip()
    item_class = item_class.strip()
    if not uid:
        raise ValueError("UID is required.")
    if not item_class:
        raise ValueError("Item class is required.")

    # Check current status
    row = conn.execute("SELECT in_stock FROM items WHERE uid = ?", (uid,)).fetchone()
    if row and row["in_stock"] == 1:
        raise ValueError(f"Item '{uid}' is already in stock.")

    # UPSERT item + log
    conn.execute(
        "INSERT INTO items (uid, item_class, in_stock) VALUES (?, ?, 1) "
        "ON CONFLICT(uid) DO UPDATE SET in_stock = 1",
        (uid, item_class),
    )
    conn.execute(
        "INSERT INTO logs (uid, action, timestamp) VALUES (?, 'ADDED', ?)",
        (uid, _dt.datetime.now().isoformat()),
    )
    log_admin_action(conn, "MANUAL_ADD", uid, user, f"Class: {item_class}")
    conn.commit()
    
    # Trigger Webhook if needed
    trigger_webhook("item_added", {"uid": uid, "class": item_class, "user": user.get("identifier")})
    
    return f"Item '{uid}' added to inventory."


def manual_remove_item(conn: sqlite3.Connection, uid: str, user: dict[str, str]) -> str:
    """Manually mark an item as REMOVED (out of stock). Returns status message."""
    uid = uid.strip()
    if not uid:
        raise ValueError("UID is required.")

    row = conn.execute("SELECT in_stock, item_class FROM items WHERE uid = ?", (uid,)).fetchone()
    if not row:
        raise ValueError(f"Item '{uid}' not found.")
    if row["in_stock"] == 0:
        raise ValueError(f"Item '{uid}' is already out of stock.")

    conn.execute("UPDATE items SET in_stock = 0 WHERE uid = ?", (uid,))
    conn.execute(
        "INSERT INTO logs (uid, action, timestamp) VALUES (?, 'REMOVED', ?)",
        (uid, _dt.datetime.now().isoformat()),
    )
    log_admin_action(conn, "MANUAL_REMOVE", uid, user, f"Class: {row['item_class']}")
    conn.commit()
    
    # Trigger Webhook
    trigger_webhook("item_removed", {"uid": uid, "class": row["item_class"], "user": user.get("identifier")})
    
    return f"Item '{uid}' removed from inventory."


def toggle_code_active(conn: sqlite3.Connection, public_uid: str, user: dict[str, str]) -> str:
    """Toggle a registered code's active status. Returns status message."""
    public_uid = public_uid.strip()
    row = conn.execute(
        "SELECT active FROM registered_codes WHERE public_uid = ?", (public_uid,)
    ).fetchone()
    if not row:
        raise ValueError(f"Code '{public_uid}' not found.")
    new_status = 0 if row["active"] == 1 else 1
    conn.execute(
        "UPDATE registered_codes SET active = ? WHERE public_uid = ?",
        (new_status, public_uid),
    )
    action_type = "CODE_ACTIVATE" if new_status == 1 else "CODE_DEACTIVATE"
    log_admin_action(conn, action_type, public_uid, user)
    conn.commit()
    label = "activated" if new_status == 1 else "deactivated"
    return f"Code '{public_uid}' {label}."


def get_registered_codes_list(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Get a simple list of active registered codes for dropdown selectors."""
    if not table_exists(conn, "registered_codes"):
        return []
    cols = column_names(conn, "registered_codes")
    if not {"public_uid", "item_class"}.issubset(cols):
        return []
    rows = conn.execute(
        "SELECT public_uid, item_class FROM registered_codes WHERE active = 1 ORDER BY item_class, public_uid"
    ).fetchall()
    return rows_to_dicts(rows)


def quote_identifier(name: str) -> str:
    if not IDENTIFIER_RE.fullmatch(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return f'"{name}"'


def row_to_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    return {key: row[key] for key in row.keys()}


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_to_dict(row) for row in rows]


def table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return [row["name"] for row in rows]


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def table_columns(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    if table not in table_names(conn):
        return []
    rows = conn.execute(f"PRAGMA table_info({quote_identifier(table)})").fetchall()
    return rows_to_dicts(rows)


def column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    return [col["name"] for col in table_columns(conn, table)]


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    if table not in table_names(conn):
        return 0
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {quote_identifier(table)}").fetchone()
    return int(row["count"]) if row else 0


def get_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    today = _dt.date.today().isoformat()
    summary: dict[str, Any] = {
        "tables": table_names(conn),
        "today": today,
        "total_items": 0,
        "in_stock_items": 0,
        "out_stock_items": 0,
        "total_logs": 0,
        "today_added": 0,
        "today_removed": 0,
        "registered_codes": 0,
        "active_codes": 0,
        "latest_movement": None,
        "inventory_by_class": [],
        "registered_by_class": [],
        "recent_logs": [],
        "active_cash_sessions": [],
    }

    if table_exists(conn, "items"):
        item_cols = column_names(conn, "items")
        if "in_stock" in item_cols:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN in_stock = 1 THEN 1 ELSE 0 END) AS in_stock,
                    SUM(CASE WHEN in_stock = 0 THEN 1 ELSE 0 END) AS out_stock
                FROM items
                """
            ).fetchone()
            summary["total_items"] = int(row["total"] or 0)
            summary["in_stock_items"] = int(row["in_stock"] or 0)
            summary["out_stock_items"] = int(row["out_stock"] or 0)

            if "item_class" in item_cols:
                summary["inventory_by_class"] = rows_to_dicts(
                    conn.execute(
                        """
                        SELECT item_class, COUNT(*) AS count
                        FROM items
                        WHERE in_stock = 1
                        GROUP BY item_class
                        ORDER BY item_class
                        """
                    ).fetchall()
                )
        else:
            row = conn.execute("SELECT COUNT(*) AS total FROM items").fetchone()
            summary["total_items"] = int(row["total"] or 0)

    if table_exists(conn, "logs"):
        row = conn.execute("SELECT COUNT(*) AS count FROM logs").fetchone()
        summary["total_logs"] = int(row["count"] or 0)

        log_cols = column_names(conn, "logs")
        if {"action", "timestamp"}.issubset(log_cols):
            row = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN action = 'ADDED' THEN 1 ELSE 0 END) AS added,
                    SUM(CASE WHEN action = 'REMOVED' THEN 1 ELSE 0 END) AS removed
                FROM logs
                WHERE substr(timestamp, 1, 10) = ?
                """,
                (today,),
            ).fetchone()
            summary["today_added"] = int(row["added"] or 0)
            summary["today_removed"] = int(row["removed"] or 0)

        if "timestamp" in log_cols:
            row = conn.execute(
                "SELECT MAX(timestamp) AS latest_movement FROM logs"
            ).fetchone()
            summary["latest_movement"] = row["latest_movement"] if row else None

        summary["recent_logs"] = get_logs(conn, {"limit": 8})["rows"]

    if table_exists(conn, "registered_codes"):
        code_cols = column_names(conn, "registered_codes")
        row = conn.execute("SELECT COUNT(*) AS count FROM registered_codes").fetchone()
        summary["registered_codes"] = int(row["count"] or 0)

        if "active" in code_cols:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM registered_codes WHERE active = 1"
            ).fetchone()
            summary["active_codes"] = int(row["count"] or 0)

        if "item_class" in code_cols:
            summary["registered_by_class"] = rows_to_dicts(
                conn.execute(
                    """
                    SELECT item_class, COUNT(*) AS count
                    FROM registered_codes
                    GROUP BY item_class
                    ORDER BY item_class
                    """
                ).fetchall()
            )

    summary["active_cash_sessions"] = get_active_cash_sessions(conn)
    return summary


def _registered_join() -> str:
    return (
        "LEFT JOIN registered_codes rc "
        "ON (l.uid = rc.public_uid OR l.uid = rc.item_class || '_' || rc.public_uid)"
    )


def _log_class_expr(conn: sqlite3.Connection) -> str:
    log_cols = column_names(conn, "logs")
    pieces: list[str] = []
    if "item_class" in log_cols:
        pieces.append("l.item_class")
    if table_exists(conn, "items") and "item_class" in column_names(conn, "items"):
        pieces.append("i.item_class")
    if table_exists(conn, "registered_codes") and "item_class" in column_names(conn, "registered_codes"):
        pieces.append("rc.item_class")
    if not pieces:
        return "''"
    return f"COALESCE({', '.join(pieces)}, '')"


def _logs_from_clause(conn: sqlite3.Connection) -> str:
    joins = ["FROM logs l"]
    if table_exists(conn, "items"):
        joins.append("LEFT JOIN items i ON i.uid = l.uid")
    if table_exists(conn, "registered_codes"):
        joins.append(_registered_join())
    return "\n".join(joins)


def _log_extra_columns(conn: sqlite3.Connection) -> list[str]:
    base = {"id", "uid", "action", "timestamp", "item_class"}
    return [col for col in column_names(conn, "logs") if col not in base]


def get_logs(conn: sqlite3.Connection, filters: dict[str, Any] | None = None) -> dict[str, Any]:
    filters = filters or {}
    if not table_exists(conn, "logs"):
        return {"rows": [], "total": 0, "columns": []}

    log_cols = column_names(conn, "logs")
    class_expr = _log_class_expr(conn)
    select_cols = [
        "l.id AS id" if "id" in log_cols else "NULL AS id",
        "l.uid AS uid" if "uid" in log_cols else "'' AS uid",
        f"{class_expr} AS item_class",
        "l.action AS action" if "action" in log_cols else "'' AS action",
        "l.timestamp AS timestamp" if "timestamp" in log_cols else "'' AS timestamp",
    ]
    extra_cols = _log_extra_columns(conn)
    select_cols.extend(f"l.{quote_identifier(col)} AS {quote_identifier(col)}" for col in extra_cols)

    where = ["1 = 1"]
    params: list[Any] = []

    uid = str(filters.get("uid") or "").strip()
    if uid and "uid" in log_cols:
        where.append("l.uid LIKE ?")
        params.append(f"%{uid}%")

    action = str(filters.get("action") or "").strip().upper()
    if action and "action" in log_cols:
        where.append("l.action = ?")
        params.append(action)

    item_class = str(filters.get("item_class") or "").strip()
    if item_class:
        where.append(f"{class_expr} = ?")
        params.append(item_class)

    date_from = str(filters.get("date_from") or filters.get("from") or "").strip()
    if date_from and "timestamp" in log_cols:
        where.append("substr(l.timestamp, 1, 10) >= ?")
        params.append(date_from)

    date_to = str(filters.get("date_to") or filters.get("to") or "").strip()
    if date_to and "timestamp" in log_cols:
        where.append("substr(l.timestamp, 1, 10) <= ?")
        params.append(date_to)

    search = str(filters.get("search") or "").strip()
    if search:
        search_parts = []
        if "uid" in log_cols:
            search_parts.append("l.uid LIKE ?")
            params.append(f"%{search}%")
        if "action" in log_cols:
            search_parts.append("l.action LIKE ?")
            params.append(f"%{search}%")
        search_parts.append(f"{class_expr} LIKE ?")
        params.append(f"%{search}%")
        where.append("(" + " OR ".join(search_parts) + ")")

    sort = str(filters.get("sort") or "timestamp").strip()
    order = "ASC" if str(filters.get("order") or "").lower() == "asc" else "DESC"
    sort_map = {
        "id": "l.id" if "id" in log_cols else "timestamp",
        "uid": "l.uid" if "uid" in log_cols else "timestamp",
        "item_class": "item_class",
        "action": "l.action" if "action" in log_cols else "timestamp",
        "timestamp": "l.timestamp" if "timestamp" in log_cols else "id",
    }
    for col in extra_cols:
        sort_map[col] = f"l.{quote_identifier(col)}"
    sort_sql = sort_map.get(sort, sort_map["timestamp"])

    limit = _positive_int(filters.get("limit"), 100, maximum=1000)
    offset = _positive_int(filters.get("offset"), 0, maximum=1_000_000)

    from_clause = _logs_from_clause(conn)
    where_sql = " AND ".join(where)
    count_sql = f"SELECT COUNT(*) AS total {from_clause} WHERE {where_sql}"
    total = int(conn.execute(count_sql, params).fetchone()["total"] or 0)

    id_tiebreaker = ", l.id DESC" if "id" in log_cols and sort != "id" else ""
    sql = f"""
        SELECT {", ".join(select_cols)}
        {from_clause}
        WHERE {where_sql}
        ORDER BY {sort_sql} {order}{id_tiebreaker}
        LIMIT ? OFFSET ?
    """
    rows = rows_to_dicts(conn.execute(sql, [*params, limit, offset]).fetchall())
    columns = ["id", "uid", "item_class", "action", "timestamp", *extra_cols]
    return {"rows": rows, "total": total, "columns": columns}


def get_inventory_items(conn: sqlite3.Connection, filters: dict[str, Any] | None = None) -> dict[str, Any]:
    filters = filters or {}
    if not table_exists(conn, "items"):
        return {"rows": [], "total": 0, "columns": []}

    item_cols = column_names(conn, "items")
    select_cols = [f"i.{quote_identifier(col)} AS {quote_identifier(col)}" for col in item_cols]
    if table_exists(conn, "logs") and "uid" in column_names(conn, "logs"):
        log_cols = column_names(conn, "logs")
        log_order = "l.id DESC" if "id" in log_cols else "l.timestamp DESC"
        select_cols.extend(
            [
                f"(SELECT l.action FROM logs l WHERE l.uid = i.uid ORDER BY {log_order} LIMIT 1) AS last_action",
                f"(SELECT l.timestamp FROM logs l WHERE l.uid = i.uid ORDER BY {log_order} LIMIT 1) AS last_seen",
                "(SELECT COUNT(*) FROM logs l WHERE l.uid = i.uid) AS movement_count",
            ]
        )
    else:
        select_cols.extend(["'' AS last_action", "'' AS last_seen", "0 AS movement_count"])

    where = ["1 = 1"]
    params: list[Any] = []
    item_class = str(filters.get("item_class") or "").strip()
    if item_class and "item_class" in item_cols:
        where.append("i.item_class = ?")
        params.append(item_class)

    in_stock = str(filters.get("in_stock") or "").strip().lower()
    if in_stock in {"1", "true", "yes", "in"} and "in_stock" in item_cols:
        where.append("i.in_stock = 1")
    elif in_stock in {"0", "false", "no", "out"} and "in_stock" in item_cols:
        where.append("i.in_stock = 0")

    search = str(filters.get("search") or "").strip()
    if search:
        parts = []
        for col in ("uid", "item_class"):
            if col in item_cols:
                parts.append(f"i.{quote_identifier(col)} LIKE ?")
                params.append(f"%{search}%")
        if parts:
            where.append("(" + " OR ".join(parts) + ")")

    sort = str(filters.get("sort") or "uid").strip()
    order = "ASC" if str(filters.get("order") or "").lower() == "asc" else "DESC"
    sort_map = {col: f"i.{quote_identifier(col)}" for col in item_cols}
    sort_map.update({"last_seen": "last_seen", "movement_count": "movement_count"})
    sort_sql = sort_map.get(sort, sort_map.get("uid", "last_seen"))
    limit = _positive_int(filters.get("limit"), 100, maximum=1000)
    offset = _positive_int(filters.get("offset"), 0, maximum=1_000_000)

    where_sql = " AND ".join(where)
    total = int(
        conn.execute(
            f"SELECT COUNT(*) AS total FROM items i WHERE {where_sql}",
            params,
        ).fetchone()["total"]
        or 0
    )
    rows = rows_to_dicts(
        conn.execute(
            f"""
            SELECT {", ".join(select_cols)}
            FROM items i
            WHERE {where_sql}
            ORDER BY {sort_sql} {order}
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()
    )
    return {"rows": rows, "total": total, "columns": [*item_cols, "last_action", "last_seen", "movement_count"]}


def get_registered_codes(conn: sqlite3.Connection, filters: dict[str, Any] | None = None) -> dict[str, Any]:
    filters = filters or {}
    if not table_exists(conn, "registered_codes"):
        return {"rows": [], "total": 0, "columns": []}

    cols = column_names(conn, "registered_codes")
    select_cols = [f"c.{quote_identifier(col)} AS {quote_identifier(col)}" for col in cols]
    where = ["1 = 1"]
    params: list[Any] = []

    item_class = str(filters.get("item_class") or "").strip()
    if item_class and "item_class" in cols:
        where.append("c.item_class = ?")
        params.append(item_class)

    active = str(filters.get("active") or "").strip().lower()
    if active in {"1", "true", "yes", "active"} and "active" in cols:
        where.append("c.active = 1")
    elif active in {"0", "false", "no", "inactive"} and "active" in cols:
        where.append("c.active = 0")

    search = str(filters.get("search") or "").strip()
    if search:
        parts = []
        for col in ("public_uid", "item_class", "payload"):
            if col in cols:
                parts.append(f"c.{quote_identifier(col)} LIKE ?")
                params.append(f"%{search}%")
        if parts:
            where.append("(" + " OR ".join(parts) + ")")

    sort = str(filters.get("sort") or "created_at").strip()
    order = "ASC" if str(filters.get("order") or "").lower() == "asc" else "DESC"
    sort_map = {col: f"c.{quote_identifier(col)}" for col in cols}
    sort_sql = sort_map.get(sort, sort_map.get("created_at", sort_map[cols[0]]))
    limit = _positive_int(filters.get("limit"), 100, maximum=1000)
    offset = _positive_int(filters.get("offset"), 0, maximum=1_000_000)

    where_sql = " AND ".join(where)
    total = int(
        conn.execute(
            f"SELECT COUNT(*) AS total FROM registered_codes c WHERE {where_sql}",
            params,
        ).fetchone()["total"]
        or 0
    )
    rows = rows_to_dicts(
        conn.execute(
            f"""
            SELECT {", ".join(select_cols)}
            FROM registered_codes c
            WHERE {where_sql}
            ORDER BY {sort_sql} {order}
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()
    )
    return {"rows": rows, "total": total, "columns": cols}


def get_table_rows(conn: sqlite3.Connection, table: str, filters: dict[str, Any] | None = None) -> dict[str, Any]:
    filters = filters or {}
    names = table_names(conn)
    if table not in names:
        raise ValueError(f"Unknown table: {table}")

    cols = column_names(conn, table)
    q_table = quote_identifier(table)
    where = ["1 = 1"]
    params: list[Any] = []

    search = str(filters.get("search") or "").strip()
    if search and cols:
        parts = []
        for col in cols:
            parts.append(f"CAST({quote_identifier(col)} AS TEXT) LIKE ?")
            params.append(f"%{search}%")
        where.append("(" + " OR ".join(parts) + ")")

    sort = str(filters.get("sort") or (cols[0] if cols else "")).strip()
    if sort not in cols:
        sort = cols[0] if cols else ""
    order = "ASC" if str(filters.get("order") or "").lower() == "asc" else "DESC"
    limit = _positive_int(filters.get("limit"), 200, maximum=5000)
    offset = _positive_int(filters.get("offset"), 0, maximum=1_000_000)
    where_sql = " AND ".join(where)

    total = int(
        conn.execute(
            f"SELECT COUNT(*) AS total FROM {q_table} WHERE {where_sql}",
            params,
        ).fetchone()["total"]
        or 0
    )
    order_sql = f"ORDER BY {quote_identifier(sort)} {order}" if sort else ""
    rows = rows_to_dicts(
        conn.execute(
            f"""
            SELECT *
            FROM {q_table}
            WHERE {where_sql}
            {order_sql}
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()
    )
    return {"rows": rows, "total": total, "columns": cols}


def trace_uid(conn: sqlite3.Connection, uid: str) -> dict[str, Any]:
    uid = uid.strip()
    candidates = {uid}
    registered: list[dict[str, Any]] = []

    if table_exists(conn, "registered_codes"):
        cols = column_names(conn, "registered_codes")
        if {"public_uid", "item_class"}.issubset(cols):
            registered = rows_to_dicts(
                conn.execute(
                    """
                    SELECT *
                    FROM registered_codes
                    WHERE public_uid = ?
                       OR item_class || '_' || public_uid = ?
                    ORDER BY created_at DESC
                    """,
                    (uid, uid),
                ).fetchall()
            )
            for row in registered:
                candidates.add(f"{row.get('item_class')}_{row.get('public_uid')}")
                candidates.add(str(row.get("public_uid") or ""))

    item = None
    if table_exists(conn, "items") and "uid" in column_names(conn, "items"):
        placeholders = ",".join("?" for _ in candidates)
        item = rows_to_dicts(
            conn.execute(
                f"SELECT * FROM items WHERE uid IN ({placeholders}) ORDER BY uid",
                tuple(candidates),
            ).fetchall()
        )

    logs: list[dict[str, Any]] = []
    if table_exists(conn, "logs") and "uid" in column_names(conn, "logs"):
        placeholders = ",".join("?" for _ in candidates)
        log_cols = column_names(conn, "logs")
        class_expr = _log_class_expr(conn)
        extra_cols = _log_extra_columns(conn)
        select_cols = [
            "l.id AS id" if "id" in log_cols else "NULL AS id",
            "l.uid AS uid",
            f"{class_expr} AS item_class",
            "l.action AS action" if "action" in log_cols else "'' AS action",
            "l.timestamp AS timestamp" if "timestamp" in log_cols else "'' AS timestamp",
        ]
        select_cols.extend(f"l.{quote_identifier(col)} AS {quote_identifier(col)}" for col in extra_cols)
        order_cols = []
        if "timestamp" in log_cols:
            order_cols.append("l.timestamp ASC")
        if "id" in log_cols:
            order_cols.append("l.id ASC")
        order_sql = ", ".join(order_cols) or "l.uid ASC"
        logs = rows_to_dicts(
            conn.execute(
                f"""
                SELECT {", ".join(select_cols)}
                {_logs_from_clause(conn)}
                WHERE l.uid IN ({placeholders})
                ORDER BY {order_sql}
                """,
                tuple(candidates),
            ).fetchall()
        )

    duplicate_actions = []
    previous = None
    for row in logs:
        action = row.get("action")
        if previous and action == previous.get("action"):
            duplicate_actions.append(
                {
                    "uid": row.get("uid"),
                    "action": action,
                    "timestamp": row.get("timestamp"),
                    "detail": "Consecutive equal actions in the timeline.",
                }
            )
        previous = row

    current_status = "unknown"
    if item:
        first = item[0]
        if "in_stock" in first:
            current_status = "in_stock" if first.get("in_stock") == 1 else "out"
    elif logs:
        current_status = "in_stock" if logs[-1].get("action") == "ADDED" else "out"

    return {
        "query": uid,
        "candidate_uids": sorted(c for c in candidates if c),
        "current_status": current_status,
        "items": item or [],
        "registered_codes": registered,
        "logs": logs,
        "anomalies": duplicate_actions,
    }


def find_anomalies(conn: sqlite3.Connection, limit: int = 200) -> list[dict[str, Any]]:
    anomalies: list[dict[str, Any]] = []

    if table_exists(conn, "logs") and {"uid", "action"}.issubset(column_names(conn, "logs")):
        rows = get_logs(conn, {"limit": 5000, "sort": "uid", "order": "asc"})["rows"]
        previous_by_uid: dict[str, dict[str, Any]] = {}
        for row in sorted(rows, key=lambda r: (str(r.get("uid") or ""), str(r.get("timestamp") or ""), int(r.get("id") or 0))):
            uid = str(row.get("uid") or "")
            previous = previous_by_uid.get(uid)
            if previous and previous.get("action") == row.get("action"):
                anomalies.append(
                    {
                        "type": "duplicate_action",
                        "severity": "warning",
                        "uid": uid,
                        "timestamp": row.get("timestamp"),
                        "detail": f"Two consecutive {row.get('action')} actions.",
                    }
                )
            previous_by_uid[uid] = row
            if len(anomalies) >= limit:
                return anomalies

    if table_exists(conn, "items") and {"uid", "in_stock"}.issubset(column_names(conn, "items")):
        items = get_inventory_items(conn, {"limit": 5000})["rows"]
        for item in items:
            last_action = item.get("last_action")
            if last_action in {"ADDED", "REMOVED"}:
                expected = 1 if last_action == "ADDED" else 0
                if item.get("in_stock") != expected:
                    anomalies.append(
                        {
                            "type": "status_mismatch",
                            "severity": "error",
                            "uid": item.get("uid"),
                            "timestamp": item.get("last_seen"),
                            "detail": f"Item in_stock={item.get('in_stock')} but last action is {last_action}.",
                        }
                    )
            elif not last_action:
                anomalies.append(
                    {
                        "type": "item_without_logs",
                        "severity": "info",
                        "uid": item.get("uid"),
                        "timestamp": None,
                        "detail": "Item exists without movement logs.",
                    }
                )
            if len(anomalies) >= limit:
                return anomalies

    if table_exists(conn, "logs") and table_exists(conn, "items"):
        log_cols = column_names(conn, "logs")
        item_cols = column_names(conn, "items")
        if "uid" in log_cols and "uid" in item_cols:
            rows = rows_to_dicts(
                conn.execute(
                    """
                    SELECT DISTINCT l.uid
                    FROM logs l
                    LEFT JOIN items i ON i.uid = l.uid
                    WHERE i.uid IS NULL
                    ORDER BY l.uid
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            )
            for row in rows:
                anomalies.append(
                    {
                        "type": "log_without_item",
                        "severity": "warning",
                        "uid": row.get("uid"),
                        "timestamp": None,
                        "detail": "Movement log exists but item is missing from items table.",
                    }
                )
                if len(anomalies) >= limit:
                    return anomalies

    return anomalies[:limit]


def get_active_cash_sessions(conn: sqlite3.Connection, limit: int = 5) -> list[dict[str, Any]]:
    if not table_exists(conn, "cash_sessions"):
        return []

    cols = column_names(conn, "cash_sessions")
    where = []
    if "status" in cols:
        where.append("lower(CAST(status AS TEXT)) IN ('active', 'open', 'opened')")
    if "closed_at" in cols:
        where.append("closed_at IS NULL")
    where_sql = " OR ".join(where) if where else "1 = 1"
    order_col = "opened_at" if "opened_at" in cols else ("id" if "id" in cols else cols[0])
    rows = conn.execute(
        f"""
        SELECT *
        FROM cash_sessions
        WHERE {where_sql}
        ORDER BY {quote_identifier(order_col)} DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return rows_to_dicts(rows)


def run_select(conn: sqlite3.Connection, sql: str, max_rows: int = 200) -> dict[str, Any]:
    statement = sql.strip().rstrip(";").strip()
    if not statement:
        return {"columns": [], "rows": [], "truncated": False}
    first = statement.split(None, 1)[0].lower()
    if first not in {"select", "with"}:
        raise ValueError("Only SELECT/CTE queries are allowed from the admin UI.")
    if ";" in statement:
        raise ValueError("Only one SQL statement is allowed.")

    cur = conn.execute(statement)
    rows = cur.fetchmany(max_rows + 1)
    columns = [desc[0] for desc in cur.description or []]
    truncated = len(rows) > max_rows
    if truncated:
        rows = rows[:max_rows]
    return {"columns": columns, "rows": rows_to_dicts(rows), "truncated": truncated}


def rows_to_csv_text(rows: list[dict[str, Any]], columns: list[str]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({col: row.get(col) for col in columns})
    return output.getvalue()


def backup_database(db_path: str | Path, output_dir: str | Path) -> Path:
    source_path = Path(db_path)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest_path = output_path / f"{source_path.stem}-{stamp}.db"

    src = sqlite3.connect(str(source_path))
    try:
        dst = sqlite3.connect(str(dest_path))
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    return dest_path


def _positive_int(value: Any, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(parsed, maximum))
