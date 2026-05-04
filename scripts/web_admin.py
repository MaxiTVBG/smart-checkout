#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import sys
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import admin_queries as aq


def main() -> int:
    parser = argparse.ArgumentParser(description="Smart Checkout SQLite web admin.")
    parser.add_argument("--db", help="Path to inventory.db. Defaults to config.yaml paths.db_path.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Use 0.0.0.0 for LAN access.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--token",
        default=os.environ.get("SMART_CHECKOUT_ADMIN_TOKEN"),
        help="Optional access token. Defaults to SMART_CHECKOUT_ADMIN_TOKEN.",
    )
    args = parser.parse_args()

    db_path = aq.resolve_db_path(args.db)
    handler = make_handler(db_path=db_path, token=args.token)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Smart Checkout admin: http://{args.host}:{args.port}/")
    print(f"Database: {db_path}")
    if args.host == "0.0.0.0" and not args.token:
        print("Warning: LAN access is open. Use --token when exposing this outside localhost.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping admin server.")
    finally:
        server.server_close()
    return 0


def make_handler(db_path: Path, token: str | None):
    class SmartCheckoutAdminHandler(BaseHTTPRequestHandler):
        server_version = "SmartCheckoutAdmin/1.0"

        def do_GET(self) -> None:
            self._dispatch()

        def do_POST(self) -> None:
            self._dispatch()

        def log_message(self, fmt: str, *args: Any) -> None:
            sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

        def _dispatch(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            params = params_from_query(parsed.query)
            if token and not self._authorized(params):
                self._send_html(self._layout("Access", "access", self._login_form(parsed.path)), HTTPStatus.UNAUTHORIZED)
                return

            try:
                if parsed.path == "/":
                    self._dashboard(params)
                elif parsed.path == "/items":
                    self._items(params)
                elif parsed.path == "/logs":
                    self._logs(params)
                elif parsed.path == "/trace":
                    self._trace(params)
                elif parsed.path == "/codes":
                    self._codes(params)
                elif parsed.path == "/tables":
                    self._tables(params)
                elif parsed.path == "/table":
                    self._table(params)
                elif parsed.path == "/sql":
                    self._sql(params)
                elif parsed.path == "/api/summary":
                    self._api_summary()
                elif parsed.path == "/api/logs":
                    self._api_logs(params)
                elif parsed.path == "/api/items":
                    self._api_items(params)
                elif parsed.path == "/api/trace":
                    self._api_trace(params)
                elif parsed.path == "/export/logs.csv":
                    self._export_logs(params)
                elif parsed.path == "/export/table.csv":
                    self._export_table(params)
                else:
                    self._send_html(self._layout("Not found", "", "<h1>Not found</h1>"), HTTPStatus.NOT_FOUND)
            except Exception as exc:
                body = f"<h1>Server error</h1><pre>{h(exc)}</pre>"
                self._send_html(self._layout("Error", "", body), HTTPStatus.INTERNAL_SERVER_ERROR)

        def _authorized(self, params: dict[str, str]) -> bool:
            if params.get("token") == token:
                return True
            return self.headers.get("X-Admin-Token") == token

        def _connect(self):
            return aq.connect_readonly(db_path)

        def _dashboard(self, params: dict[str, str]) -> None:
            with self._connect() as conn:
                summary = aq.get_summary(conn)
                anomalies = aq.find_anomalies(conn, limit=5)
            metrics = [
                ("In stock", summary["in_stock_items"]),
                ("Out", summary["out_stock_items"]),
                ("Logs today", summary["today_added"] + summary["today_removed"]),
                ("Registered", summary["registered_codes"]),
            ]
            metric_html = "".join(
                f"<div class='metric'><span>{h(label)}</span><strong>{h(value)}</strong></div>"
                for label, value in metrics
            )
            inventory_html = table_html(summary["inventory_by_class"], ["item_class", "count"])
            recent_html = table_html(
                summary["recent_logs"],
                ["id", "uid", "item_class", "action", "timestamp"],
                trace_links=True,
                url=self._url,
            )
            session_html = (
                table_html(summary["active_cash_sessions"], columns_from_rows(summary["active_cash_sessions"]))
                if summary["active_cash_sessions"]
                else "<p class='muted'>No active cash session table/row yet.</p>"
            )
            anomaly_html = (
                table_html(anomalies, ["severity", "type", "uid", "timestamp", "detail"], trace_links=True, url=self._url)
                if anomalies
                else "<p class='muted'>No obvious anomalies in the current checks.</p>"
            )
            body = f"""
            <section class="metrics">{metric_html}</section>
            <section class="grid two">
              <div>
                <div class="section-head"><h2>Stock by class</h2><a href="{self._url('/items')}">Open</a></div>
                {inventory_html}
              </div>
              <div>
                <div class="section-head"><h2>Active cash session</h2></div>
                {session_html}
              </div>
            </section>
            <section>
              <div class="section-head"><h2>Recent movements</h2><a href="{self._url('/logs')}">Open all</a></div>
              {recent_html}
            </section>
            <section>
              <div class="section-head"><h2>Checks</h2><a href="{self._url('/sql')}">SQL</a></div>
              {anomaly_html}
            </section>
            """
            self._send_html(self._layout("Dashboard", "dashboard", body))

        def _items(self, params: dict[str, str]) -> None:
            filters = {
                "item_class": params.get("class"),
                "in_stock": params.get("in_stock"),
                "search": params.get("search"),
                "sort": params.get("sort") or "uid",
                "order": params.get("order") or "asc",
                "limit": params.get("limit") or "200",
            }
            with self._connect() as conn:
                result = aq.get_inventory_items(conn, filters)
            body = f"""
            <div class="section-head"><h1>Inventory</h1><span class="muted">{result['total']} rows</span></div>
            {self._item_filters(params)}
            {table_html(result["rows"], result["columns"], trace_links=True, url=self._url)}
            """
            self._send_html(self._layout("Inventory", "items", body))

        def _logs(self, params: dict[str, str]) -> None:
            filters = {
                "uid": params.get("uid"),
                "action": params.get("action"),
                "item_class": params.get("class"),
                "date_from": params.get("from"),
                "date_to": params.get("to"),
                "search": params.get("search"),
                "sort": params.get("sort") or "timestamp",
                "order": params.get("order") or "desc",
                "limit": params.get("limit") or "200",
            }
            with self._connect() as conn:
                result = aq.get_logs(conn, filters)
            export_url = self._url("/export/logs.csv", **params)
            body = f"""
            <div class="section-head"><h1>Movements</h1><a href="{export_url}">CSV</a></div>
            <p class="muted">{result['total']} matching rows</p>
            {self._log_filters(params)}
            {table_html(result["rows"], result["columns"], trace_links=True, url=self._url)}
            """
            self._send_html(self._layout("Movements", "logs", body))

        def _trace(self, params: dict[str, str]) -> None:
            uid = (params.get("uid") or "").strip()
            form = f"""
            <form class="filters" method="get" action="{self._url('/trace')}">
              {self._token_input()}
              <label>UID <input name="uid" value="{h(uid)}" placeholder="led_box_BC418EA5 or BC418EA5"></label>
              <button type="submit">Trace</button>
            </form>
            """
            if not uid:
                body = f"<h1>Trace item</h1>{form}"
            else:
                with self._connect() as conn:
                    trace = aq.trace_uid(conn, uid)
                body = f"""
                <div class="section-head"><h1>Trace</h1><span class="status">{h(trace['current_status'])}</span></div>
                {form}
                <p class="muted">Candidates: {h(', '.join(trace['candidate_uids']) or '-')}</p>
                <section><h2>Item</h2>{table_html(trace['items'], columns_from_rows(trace['items']))}</section>
                <section><h2>Registered code</h2>{table_html(trace['registered_codes'], columns_from_rows(trace['registered_codes']))}</section>
                <section><h2>Timeline</h2>{table_html(trace['logs'], columns_from_rows(trace['logs'], ['id','uid','item_class','action','timestamp']))}</section>
                <section><h2>Trace checks</h2>{table_html(trace['anomalies'], ['uid','action','timestamp','detail']) if trace['anomalies'] else "<p class='muted'>No trace anomalies.</p>"}</section>
                """
            self._send_html(self._layout("Trace", "trace", body))

        def _codes(self, params: dict[str, str]) -> None:
            filters = {
                "item_class": params.get("class"),
                "active": params.get("active"),
                "search": params.get("search"),
                "sort": params.get("sort") or "created_at",
                "order": params.get("order") or "desc",
                "limit": params.get("limit") or "200",
            }
            with self._connect() as conn:
                result = aq.get_registered_codes(conn, filters)
            body = f"""
            <div class="section-head"><h1>Registered codes</h1><span class="muted">{result['total']} rows</span></div>
            {self._code_filters(params)}
            {table_html(result["rows"], result["columns"], trace_links=True, url=self._url)}
            """
            self._send_html(self._layout("Codes", "codes", body))

        def _tables(self, params: dict[str, str]) -> None:
            with self._connect() as conn:
                rows = [
                    {"table": name, "rows": aq.count_rows(conn, name)}
                    for name in aq.table_names(conn)
                ]
            body = f"""
            <div class="section-head"><h1>Tables</h1></div>
            {table_html(rows, ["table", "rows"], table_links=True, url=self._url)}
            """
            self._send_html(self._layout("Tables", "tables", body))

        def _table(self, params: dict[str, str]) -> None:
            table = params.get("name") or ""
            if not table:
                self._send_html(self._layout("Table", "tables", "<h1>Missing table name</h1>"), HTTPStatus.BAD_REQUEST)
                return
            filters = {
                "search": params.get("search"),
                "sort": params.get("sort"),
                "order": params.get("order") or "desc",
                "limit": params.get("limit") or "200",
            }
            with self._connect() as conn:
                result = aq.get_table_rows(conn, table, filters)
                schema = aq.table_columns(conn, table)
            export_url = self._url("/export/table.csv", **params)
            body = f"""
            <div class="section-head"><h1>{h(table)}</h1><a href="{export_url}">CSV</a></div>
            <p class="muted">{result['total']} rows</p>
            {self._table_filters(table, params)}
            <section><h2>Rows</h2>{table_html(result["rows"], result["columns"], trace_links=True, url=self._url)}</section>
            <section><h2>Schema</h2>{table_html(schema, ["cid","name","type","notnull","dflt_value","pk"])}</section>
            """
            self._send_html(self._layout("Table", "tables", body))

        def _sql(self, params: dict[str, str]) -> None:
            if self.command == "POST":
                length = int(self.headers.get("Content-Length", "0") or 0)
                body_bytes = self.rfile.read(length)
                posted = urllib.parse.parse_qs(body_bytes.decode("utf-8"), keep_blank_values=True)
                sql = posted.get("sql", [""])[0]
                if token and "token" in posted:
                    params["token"] = posted["token"][0]
            else:
                sql = params.get("sql") or "SELECT * FROM logs ORDER BY id DESC LIMIT 50"

            result_html = ""
            error_html = ""
            if sql.strip():
                try:
                    with self._connect() as conn:
                        result = aq.run_select(conn, sql, max_rows=300)
                    result_html = table_html(result["rows"], result["columns"], trace_links=True, url=self._url)
                    if result["truncated"]:
                        result_html = "<p class='muted'>Result truncated to 300 rows.</p>" + result_html
                except Exception as exc:
                    error_html = f"<p class='error'>{h(exc)}</p>"

            body = f"""
            <div class="section-head"><h1>SQL</h1><span class="muted">SELECT only</span></div>
            <form class="sql-form" method="post" action="{self._url('/sql')}">
              {self._token_input()}
              <textarea name="sql" spellcheck="false">{h(sql)}</textarea>
              <button type="submit">Run</button>
            </form>
            {error_html}
            {result_html}
            """
            self._send_html(self._layout("SQL", "sql", body))

        def _api_summary(self) -> None:
            with self._connect() as conn:
                self._send_json(aq.get_summary(conn))

        def _api_logs(self, params: dict[str, str]) -> None:
            with self._connect() as conn:
                self._send_json(aq.get_logs(conn, params))

        def _api_items(self, params: dict[str, str]) -> None:
            with self._connect() as conn:
                self._send_json(aq.get_inventory_items(conn, params))

        def _api_trace(self, params: dict[str, str]) -> None:
            uid = params.get("uid") or ""
            with self._connect() as conn:
                self._send_json(aq.trace_uid(conn, uid))

        def _export_logs(self, params: dict[str, str]) -> None:
            with self._connect() as conn:
                result = aq.get_logs(conn, params)
            self._send_csv("logs.csv", aq.rows_to_csv_text(result["rows"], result["columns"]))

        def _export_table(self, params: dict[str, str]) -> None:
            table = params.get("name") or ""
            with self._connect() as conn:
                result = aq.get_table_rows(conn, table, params)
            self._send_csv(f"{table}.csv", aq.rows_to_csv_text(result["rows"], result["columns"]))

        def _layout(self, title: str, active: str, body: str) -> str:
            nav_items = [
                ("dashboard", "/", "Dashboard"),
                ("items", "/items", "Inventory"),
                ("logs", "/logs", "Movements"),
                ("trace", "/trace", "Trace"),
                ("codes", "/codes", "Codes"),
                ("tables", "/tables", "Tables"),
                ("sql", "/sql", "SQL"),
            ]
            nav = "".join(
                f"<a class=\"{'active' if key == active else ''}\" href=\"{self._url(path)}\">{label}</a>"
                for key, path, label in nav_items
            )
            return f"""<!doctype html>
            <html lang="bg">
            <head>
              <meta charset="utf-8">
              <meta name="viewport" content="width=device-width, initial-scale=1">
              <title>{h(title)} - Smart Checkout</title>
              <style>{CSS}</style>
            </head>
            <body>
              <header>
                <div>
                  <strong>Smart Checkout</strong>
                  <span>{h(str(db_path))}</span>
                </div>
                <nav>{nav}</nav>
              </header>
              <main>{body}</main>
            </body>
            </html>"""

        def _url(self, path: str, **params: Any) -> str:
            clean = {k: v for k, v in params.items() if v not in (None, "")}
            parsed = urllib.parse.urlparse(self.path)
            current = params_from_query(parsed.query)
            if token and current.get("token"):
                clean.setdefault("token", current["token"])
            query = urllib.parse.urlencode(clean, doseq=True)
            return path + (f"?{query}" if query else "")

        def _token_input(self) -> str:
            if not token:
                return ""
            parsed = urllib.parse.urlparse(self.path)
            current = params_from_query(parsed.query)
            value = current.get("token", "")
            return f'<input type="hidden" name="token" value="{h(value)}">'

        def _login_form(self, path: str) -> str:
            return f"""
            <h1>Access token</h1>
            <form class="filters" method="get" action="{h(path)}">
              <label>Token <input name="token" type="password" autofocus></label>
              <button type="submit">Open</button>
            </form>
            """

        def _log_filters(self, params: dict[str, str]) -> str:
            return f"""
            <form class="filters" method="get" action="{self._url('/logs')}">
              {self._token_input()}
              <label>Search <input name="search" value="{h(params.get('search',''))}"></label>
              <label>UID <input name="uid" value="{h(params.get('uid',''))}"></label>
              <label>Class <input name="class" value="{h(params.get('class',''))}"></label>
              <label>Action <select name="action">{options(['','ADDED','REMOVED'], params.get('action',''))}</select></label>
              <label>From <input type="date" name="from" value="{h(params.get('from',''))}"></label>
              <label>To <input type="date" name="to" value="{h(params.get('to',''))}"></label>
              <label>Sort <input name="sort" value="{h(params.get('sort','timestamp'))}"></label>
              <label>Order <select name="order">{options(['desc','asc'], params.get('order','desc'))}</select></label>
              <button type="submit">Apply</button>
            </form>
            """

        def _item_filters(self, params: dict[str, str]) -> str:
            return f"""
            <form class="filters" method="get" action="{self._url('/items')}">
              {self._token_input()}
              <label>Search <input name="search" value="{h(params.get('search',''))}"></label>
              <label>Class <input name="class" value="{h(params.get('class',''))}"></label>
              <label>Status <select name="in_stock">{options(['','yes','no'], params.get('in_stock',''))}</select></label>
              <label>Sort <select name="sort">{options(['uid','item_class','in_stock','last_seen','movement_count'], params.get('sort','uid'))}</select></label>
              <label>Order <select name="order">{options(['asc','desc'], params.get('order','asc'))}</select></label>
              <button type="submit">Apply</button>
            </form>
            """

        def _code_filters(self, params: dict[str, str]) -> str:
            return f"""
            <form class="filters" method="get" action="{self._url('/codes')}">
              {self._token_input()}
              <label>Search <input name="search" value="{h(params.get('search',''))}"></label>
              <label>Class <input name="class" value="{h(params.get('class',''))}"></label>
              <label>Active <select name="active">{options(['','yes','no'], params.get('active',''))}</select></label>
              <button type="submit">Apply</button>
            </form>
            """

        def _table_filters(self, table: str, params: dict[str, str]) -> str:
            return f"""
            <form class="filters" method="get" action="{self._url('/table')}">
              {self._token_input()}
              <input type="hidden" name="name" value="{h(table)}">
              <label>Search <input name="search" value="{h(params.get('search',''))}"></label>
              <label>Sort <input name="sort" value="{h(params.get('sort',''))}"></label>
              <label>Order <select name="order">{options(['desc','asc'], params.get('order','desc'))}</select></label>
              <button type="submit">Apply</button>
            </form>
            """

        def _send_html(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, payload: Any) -> None:
            data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_csv(self, filename: str, text: str) -> None:
            data = text.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return SmartCheckoutAdminHandler


def params_from_query(query: str) -> dict[str, str]:
    parsed = urllib.parse.parse_qs(query, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def h(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def options(values: list[str], selected: str) -> str:
    labels = {"": "All", "yes": "Yes", "no": "No", "asc": "Asc", "desc": "Desc"}
    return "".join(
        f'<option value="{h(value)}"{" selected" if value == selected else ""}>{h(labels.get(value, value))}</option>'
        for value in values
    )


def columns_from_rows(rows: list[dict[str, Any]], fallback: list[str] | None = None) -> list[str]:
    if rows:
        columns: list[str] = []
        for row in rows:
            for key in row:
                if key not in columns:
                    columns.append(key)
        return columns
    return fallback or []


def table_html(
    rows: list[dict[str, Any]],
    columns: list[str],
    trace_links: bool = False,
    table_links: bool = False,
    url=None,
) -> str:
    columns = columns or columns_from_rows(rows)
    if not columns:
        return "<p class='muted'>No columns.</p>"
    if not rows:
        return "<p class='muted'>No rows.</p>"
    head = "".join(f"<th>{h(col)}</th>" for col in columns)
    body_rows = []
    for row in rows:
        cells = []
        for col in columns:
            value = row.get(col)
            cell = h(value)
            if trace_links and col == "uid" and value and url:
                cell = f'<a href="{url("/trace", uid=value)}">{h(value)}</a>'
            if table_links and col == "table" and value and url:
                cell = f'<a href="{url("/table", name=value)}">{h(value)}</a>'
            if col == "in_stock":
                cell = '<span class="status in">IN</span>' if value == 1 else '<span class="status out">OUT</span>'
            if col == "active":
                cell = '<span class="status in">YES</span>' if value == 1 else '<span class="status out">NO</span>'
            cells.append(f"<td>{cell}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    return f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table></div>"


CSS = """
:root {
  color-scheme: light;
  --bg: #f6f7f9;
  --surface: #ffffff;
  --line: #d9dde5;
  --text: #17202a;
  --muted: #687385;
  --accent: #116d6e;
  --accent-soft: #e6f3f2;
  --danger: #b42318;
  --ok: #087443;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 14px;
}
header {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 12px 24px;
  border-bottom: 1px solid var(--line);
  background: rgba(255,255,255,0.96);
}
header strong { display: block; font-size: 16px; }
header span { color: var(--muted); font-size: 12px; }
nav { display: flex; flex-wrap: wrap; gap: 6px; }
nav a, .section-head a, button {
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--text);
  border-radius: 6px;
  padding: 7px 10px;
  text-decoration: none;
  font: inherit;
}
nav a.active, button {
  border-color: var(--accent);
  background: var(--accent);
  color: #fff;
}
main {
  width: min(1480px, calc(100vw - 32px));
  margin: 20px auto 48px;
}
section { margin-top: 22px; }
h1, h2 { margin: 0; letter-spacing: 0; }
h1 { font-size: 24px; }
h2 { font-size: 17px; }
.muted { color: var(--muted); }
.error { color: var(--danger); font-weight: 600; }
.metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}
.metric {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px;
}
.metric span { display: block; color: var(--muted); }
.metric strong { display: block; margin-top: 8px; font-size: 28px; }
.grid.two {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 18px;
}
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 0 0 10px;
}
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: end;
  padding: 12px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 8px;
  margin-bottom: 14px;
}
label { display: grid; gap: 4px; color: var(--muted); font-size: 12px; }
input, select, textarea {
  min-height: 34px;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 7px 9px;
  background: #fff;
  color: var(--text);
  font: inherit;
}
textarea {
  width: 100%;
  min-height: 140px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.sql-form {
  display: grid;
  gap: 10px;
  padding: 12px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 8px;
}
.sql-form button { justify-self: start; }
.table-wrap {
  overflow: auto;
  border: 1px solid var(--line);
  background: var(--surface);
  border-radius: 8px;
}
table {
  width: 100%;
  border-collapse: collapse;
  white-space: nowrap;
}
th, td {
  padding: 9px 10px;
  border-bottom: 1px solid var(--line);
  text-align: left;
  vertical-align: top;
}
th {
  position: sticky;
  top: 0;
  background: #eef1f5;
  color: #374151;
  font-weight: 650;
}
td {
  max-width: 360px;
  overflow: hidden;
  text-overflow: ellipsis;
}
td a { color: var(--accent); font-weight: 600; }
.status {
  display: inline-block;
  border-radius: 999px;
  padding: 3px 8px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 12px;
  font-weight: 700;
}
.status.in { background: #e8f5ee; color: var(--ok); }
.status.out { background: #fdecec; color: var(--danger); }
@media (max-width: 860px) {
  header { align-items: flex-start; flex-direction: column; padding: 12px; }
  main { width: calc(100vw - 20px); margin-top: 12px; }
  .metrics, .grid.two { grid-template-columns: 1fr; }
  .filters { display: grid; }
  label, input, select, button { width: 100%; }
}
"""


if __name__ == "__main__":
    raise SystemExit(main())
