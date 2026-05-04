#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import admin_queries as aq


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Smart Checkout database reports, tracing, exports, and backups."
    )
    parser.add_argument("--db", help="Path to inventory.db. Defaults to config.yaml paths.db_path.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    summary_parser = subparsers.add_parser("summary", help="Show stock and movement totals.")
    summary_parser.add_argument("--json", action="store_true", help="Print JSON.")

    logs_parser = subparsers.add_parser("logs", help="List movement logs with filters and sorting.")
    add_log_filters(logs_parser)
    logs_parser.add_argument("--csv", action="store_true", help="Print CSV instead of a table.")
    logs_parser.add_argument("--out", help="Write CSV output to this file.")

    items_parser = subparsers.add_parser("items", help="List current item states.")
    items_parser.add_argument("--class", dest="item_class", help="Filter by item class.")
    items_parser.add_argument(
        "--in-stock",
        choices=["all", "yes", "no"],
        default="all",
        help="Filter by current stock state.",
    )
    items_parser.add_argument("--search", help="Search UID/class.")
    items_parser.add_argument(
        "--sort",
        default="uid",
        help="Sort by uid, item_class, in_stock, last_seen, or movement_count.",
    )
    items_parser.add_argument("--asc", action="store_true", help="Sort ascending.")
    items_parser.add_argument("--limit", type=int, default=100)
    items_parser.add_argument("--csv", action="store_true")
    items_parser.add_argument("--out", help="Write CSV output to this file.")

    trace_parser = subparsers.add_parser("trace", help="Trace one item by inventory UID or public UID.")
    trace_parser.add_argument("uid", help="Example: led_box_BC418EA5 or BC418EA5.")
    trace_parser.add_argument("--json", action="store_true")

    anomalies_parser = subparsers.add_parser("anomalies", help="Find suspicious database states.")
    anomalies_parser.add_argument("--limit", type=int, default=200)
    anomalies_parser.add_argument("--json", action="store_true")

    schema_parser = subparsers.add_parser("schema", help="Show tables and columns.")
    schema_parser.add_argument("--json", action="store_true")

    export_table_parser = subparsers.add_parser("export-table", help="Export a full table to CSV.")
    export_table_parser.add_argument("table", help="Table name.")
    export_table_parser.add_argument("--search", help="Search all columns.")
    export_table_parser.add_argument("--sort", help="Sort column.")
    export_table_parser.add_argument("--asc", action="store_true")
    export_table_parser.add_argument("--limit", type=int, default=5000)
    export_table_parser.add_argument("--out", required=True, help="Output CSV file.")

    backup_parser = subparsers.add_parser("backup", help="Create a consistent SQLite backup copy.")
    backup_parser.add_argument(
        "--out-dir",
        default="data/backups",
        help="Backup destination directory.",
    )

    args = parser.parse_args()
    db_path = aq.resolve_db_path(args.db)

    if args.command == "backup":
        backup_path = aq.backup_database(db_path, ROOT / args.out_dir)
        print(f"Backup written: {backup_path}")
        return 0

    with aq.connect_readonly(db_path) as conn:
        if args.command == "summary":
            summary = aq.get_summary(conn)
            if args.json:
                print_json(summary)
            else:
                print_summary(summary)
        elif args.command == "logs":
            result = aq.get_logs(conn, log_filters_from_args(args))
            output_rows(result["rows"], result["columns"], csv_mode=args.csv, out=args.out)
        elif args.command == "items":
            filters = {
                "item_class": args.item_class,
                "in_stock": "" if args.in_stock == "all" else args.in_stock,
                "search": args.search,
                "sort": args.sort,
                "order": "asc" if args.asc else "desc",
                "limit": args.limit,
            }
            result = aq.get_inventory_items(conn, filters)
            output_rows(result["rows"], result["columns"], csv_mode=args.csv, out=args.out)
        elif args.command == "trace":
            trace = aq.trace_uid(conn, args.uid)
            if args.json:
                print_json(trace)
            else:
                print_trace(trace)
        elif args.command == "anomalies":
            anomalies = aq.find_anomalies(conn, limit=args.limit)
            if args.json:
                print_json(anomalies)
            else:
                output_rows(
                    anomalies,
                    ["severity", "type", "uid", "timestamp", "detail"],
                    csv_mode=False,
                    out=None,
                )
        elif args.command == "schema":
            schema = get_schema(conn)
            if args.json:
                print_json(schema)
            else:
                print_schema(schema)
        elif args.command == "export-table":
            result = aq.get_table_rows(
                conn,
                args.table,
                {
                    "search": args.search,
                    "sort": args.sort,
                    "order": "asc" if args.asc else "desc",
                    "limit": args.limit,
                },
            )
            output_rows(result["rows"], result["columns"], csv_mode=True, out=args.out)

    return 0


def add_log_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--uid", help="Filter by UID substring.")
    parser.add_argument("--action", choices=["ADDED", "REMOVED"], help="Filter action.")
    parser.add_argument("--class", dest="item_class", help="Filter item class.")
    parser.add_argument("--from", dest="date_from", help="Start date YYYY-MM-DD.")
    parser.add_argument("--to", dest="date_to", help="End date YYYY-MM-DD.")
    parser.add_argument("--search", help="Search UID/action/class.")
    parser.add_argument(
        "--sort",
        default="timestamp",
        help="Sort by id, uid, item_class, action, timestamp, or future log columns.",
    )
    parser.add_argument("--asc", action="store_true", help="Sort ascending.")
    parser.add_argument("--limit", type=int, default=100)


def log_filters_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "uid": args.uid,
        "action": args.action,
        "item_class": args.item_class,
        "date_from": args.date_from,
        "date_to": args.date_to,
        "search": args.search,
        "sort": args.sort,
        "order": "asc" if args.asc else "desc",
        "limit": args.limit,
    }


def print_summary(summary: dict[str, Any]) -> None:
    print("Smart Checkout summary")
    print(f"Date: {summary.get('today')}")
    print(f"Tracked items: {summary.get('total_items')} total")
    print(f"In stock: {summary.get('in_stock_items')} | Out: {summary.get('out_stock_items')}")
    print(f"Registered codes: {summary.get('registered_codes')} | Active: {summary.get('active_codes')}")
    print(f"Movement logs: {summary.get('total_logs')}")
    print(f"Today ADDED: {summary.get('today_added')} | REMOVED: {summary.get('today_removed')}")
    print(f"Latest movement: {summary.get('latest_movement') or '-'}")
    print()
    output_rows(summary.get("inventory_by_class") or [], ["item_class", "count"], csv_mode=False, out=None)


def print_trace(trace: dict[str, Any]) -> None:
    print(f"Trace: {trace.get('query')}")
    print(f"Candidate UIDs: {', '.join(trace.get('candidate_uids') or []) or '-'}")
    print(f"Current status: {trace.get('current_status')}")
    print()
    print("Items")
    item_columns = columns_from_rows(trace.get("items") or [])
    output_rows(trace.get("items") or [], item_columns, csv_mode=False, out=None)
    print()
    print("Registered codes")
    code_columns = columns_from_rows(trace.get("registered_codes") or [])
    output_rows(trace.get("registered_codes") or [], code_columns, csv_mode=False, out=None)
    print()
    print("Timeline")
    output_rows(
        trace.get("logs") or [],
        columns_from_rows(trace.get("logs") or [], fallback=["id", "uid", "item_class", "action", "timestamp"]),
        csv_mode=False,
        out=None,
    )
    if trace.get("anomalies"):
        print()
        print("Trace anomalies")
        output_rows(trace["anomalies"], ["uid", "action", "timestamp", "detail"], csv_mode=False, out=None)


def get_schema(conn) -> list[dict[str, Any]]:
    schema = []
    for table in aq.table_names(conn):
        schema.append(
            {
                "table": table,
                "rows": aq.count_rows(conn, table),
                "columns": aq.table_columns(conn, table),
            }
        )
    return schema


def print_schema(schema: list[dict[str, Any]]) -> None:
    for table in schema:
        print(f"{table['table']} ({table['rows']} rows)")
        for col in table["columns"]:
            required = " NOT NULL" if col.get("notnull") else ""
            pk = " PRIMARY KEY" if col.get("pk") else ""
            print(f"  - {col['name']} {col['type']}{required}{pk}")
        print()


def output_rows(rows: list[dict[str, Any]], columns: list[str], csv_mode: bool, out: str | None) -> None:
    columns = columns or columns_from_rows(rows)
    if csv_mode or out:
        csv_text = aq.rows_to_csv_text(rows, columns)
        if out:
            Path(out).write_text(csv_text, encoding="utf-8")
            print(f"CSV written: {out}")
        else:
            print(csv_text, end="")
        return
    print_table(rows, columns)


def print_table(rows: list[dict[str, Any]], columns: list[str]) -> None:
    if not columns:
        print("(no columns)")
        return
    if not rows:
        print("(no rows)")
        return

    widths = {}
    for col in columns:
        values = [display_value(row.get(col)) for row in rows]
        widths[col] = min(max([len(col), *(len(value) for value in values)]), 48)

    header = " | ".join(col.ljust(widths[col]) for col in columns)
    print(header)
    print("-+-".join("-" * widths[col] for col in columns))
    for row in rows:
        print(" | ".join(truncate(display_value(row.get(col)), widths[col]).ljust(widths[col]) for col in columns))


def columns_from_rows(rows: list[dict[str, Any]], fallback: list[str] | None = None) -> list[str]:
    if rows:
        seen = []
        for row in rows:
            for key in row.keys():
                if key not in seen:
                    seen.append(key)
        return seen
    return fallback or []


def display_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def truncate(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    return value[: width - 3] + "..."


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    raise SystemExit(main())
