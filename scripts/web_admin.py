#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import sys
import time as _time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
import yaml

COOKIE_NAME = "sc_session"
COOKIE_MAX_AGE = 86400  # 24 hours

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import admin_queries as aq


ALL_PERMISSIONS = [
    "view_dashboard", "view_inventory", "view_logs", "view_trace",
    "view_tables", "manual_add", "manual_remove", "manage_codes",
    "run_sql", "manage_users", "export_data",
]


def _resolve_permissions(role_name: str, roles_def: dict) -> set[str]:
    """Resolve a role name to a set of permission strings."""
    role_cfg = roles_def.get(role_name, {})
    perms = role_cfg.get("permissions", [])
    if "*" in perms:
        return set(ALL_PERMISSIONS)
    return set(perms)


def main() -> int:
    config_path = ROOT / "config.yaml"
    cfg_host = "127.0.0.1"
    cfg_port = 8000
    web_cfg: dict[str, Any] = {}

    if config_path.exists():
        try:
            full_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            web_cfg = full_config.get("web_admin", {})
            cfg_host = str(web_cfg.get("host", cfg_host))
            cfg_port = int(web_cfg.get("port", cfg_port))
        except Exception as e:
            print(f"Warning: could not parse config.yaml: {e}")

    parser = argparse.ArgumentParser(description="Smart Checkout SQLite web admin.")
    parser.add_argument("--db", help="Path to inventory.db.")
    parser.add_argument("--host", default=cfg_host)
    parser.add_argument("--port", type=int, default=cfg_port)
    args = parser.parse_args()

    session_secret = secrets.token_hex(32)
    db_path = aq.resolve_db_path(args.db)
    handler = make_handler(
        db_path=db_path,
        web_cfg=web_cfg,
        session_secret=session_secret,
        config_path=config_path,
        bind_host=args.host,
        bind_port=args.port,
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)

    print(f"\n{'='*52}")
    print(f"  Smart Checkout Admin")
    print(f"{'='*52}")
    if args.host == "0.0.0.0":
        # Try to find the local LAN IP for a friendly display message
        import socket
        try:
            lan_ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            lan_ip = "<your-local-ip>"
        print(f"  Local:    http://127.0.0.1:{args.port}/")
        print(f"  Network:  http://{lan_ip}:{args.port}/")
        print(f"  All network interfaces bound (0.0.0.0)")
        if not admin_tokens and not viewer_tokens:
            print(f"\n  ⚠  WARNING: No tokens set! Open access for everyone.")
            print(f"  Set admin_tokens in config.yaml web_admin section.")
    else:
        print(f"  URL:      http://{args.host}:{args.port}/")
    print(f"  Database: {db_path}")
    print(f"{'='*52}\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping admin server.")
    finally:
        server.server_close()
    return 0


def _sign_cookie(role: str, secret: str) -> str:
    """Create a signed cookie value: role:timestamp:signature"""
    ts = str(int(_time.time()))
    payload = f"{role}:{ts}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def _verify_cookie(value: str, secret: str) -> str | None:
    """Verify a signed cookie. Returns role if valid, None otherwise."""
    parts = value.split(":")
    if len(parts) != 3:
        return None
    role, ts, sig = parts
    # Accept any role name (admin, manager, viewer, etc.)
    try:
        created = int(ts)
    except ValueError:
        return None
    if _time.time() - created > COOKIE_MAX_AGE:
        return None  # Expired
    expected = hmac.new(secret.encode(), f"{role}:{ts}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    return role


def make_handler(db_path: Path, web_cfg: dict[str, Any],
                  session_secret: str, config_path: Path,
                  bind_host: str = "127.0.0.1", bind_port: int = 8000):

    # Parse roles & users from config
    roles_def = web_cfg.get("roles", {})
    token_users = web_cfg.get("users", [])  # [{token, role}, ...]

    # Build token → role lookup
    token_to_role: dict[str, str] = {}
    for u in token_users:
        t = u.get("token", "")
        r = u.get("role", "")
        if t and r:
            token_to_role[t] = r

    # OAuth config
    oauth_config = web_cfg.get("google_oauth", {})
    oauth_client_id = oauth_config.get("client_id", "")
    oauth_client_secret = oauth_config.get("client_secret", "")
    oauth_users = oauth_config.get("users", [])  # [{email, role}, ...]
    oauth_email_to_role: dict[str, str] = {}
    for u in oauth_users:
        e = u.get("email", "").lower()
        r = u.get("role", "")
        if e and r:
            oauth_email_to_role[e] = r
    oauth_enabled = bool(oauth_client_id and oauth_client_secret)

    def _build_redirect_uri(host_header: str) -> str:
        host = host_header.split(":")[0] if host_header else bind_host
        port = host_header.split(":")[1] if host_header and ":" in host_header else str(bind_port)
        try:
            parts = host.split(".")
            if len(parts) == 4 and all(p.isdigit() for p in parts):
                host = f"{host}.nip.io"
        except Exception:
            pass
        return f"http://{host}:{port}/auth/google/callback"

    def _has_perm(role: str, perm: str) -> bool:
        return perm in _resolve_permissions(role, roles_def)

    def _save_config(updated_web_cfg: dict) -> None:
        """Write updated web_admin config back to config.yaml."""
        try:
            full = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            full["web_admin"] = updated_web_cfg
            config_path.write_text(yaml.dump(full, allow_unicode=True, default_flow_style=False, sort_keys=False), encoding="utf-8")
        except Exception as exc:
            raise ValueError(f"Failed to save config: {exc}")

    class SmartCheckoutAdminHandler(BaseHTTPRequestHandler):
        server_version = "SmartCheckoutAdmin/3.0"

        def do_GET(self) -> None:
            self._dispatch()

        def do_POST(self) -> None:
            self._dispatch()

        def log_message(self, fmt: str, *args: Any) -> None:
            sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

        def _is_local_request(self) -> bool:
            client_ip = self.client_address[0]
            return client_ip in ("127.0.0.1", "::1", "localhost")

        def _get_role_from_cookie(self) -> str | None:
            if not token_to_role and not oauth_email_to_role:
                return "admin"
            cookie_header = self.headers.get("Cookie", "")
            cookie = SimpleCookie()
            try:
                cookie.load(cookie_header)
            except Exception:
                return None
            morsel = cookie.get(COOKIE_NAME)
            if not morsel:
                return None
            role = _verify_cookie(morsel.value, session_secret)
            if role and role not in roles_def:
                return None
            return role

        def _read_post_body(self) -> dict[str, list[str]]:
            length = int(self.headers.get("Content-Length", "0") or 0)
            body_bytes = self.rfile.read(length)
            return urllib.parse.parse_qs(body_bytes.decode("utf-8"), keep_blank_values=True)

        def _dispatch(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            params = params_from_query(parsed.query)
            self._pending_cookies: list[str] = []

            # Handle login POST (token auth, local only)
            if parsed.path == "/login" and self.command == "POST":
                self._handle_login()
                return

            # Handle logout
            if parsed.path == "/logout":
                self._handle_logout()
                return

            # Google OAuth routes
            if parsed.path == "/auth/google":
                self._handle_google_auth_redirect()
                return
            if parsed.path == "/auth/google/callback":
                self._handle_google_callback(params)
                return

            role = self._get_role_from_cookie()

            # Read POST body for other endpoints (e.g. /sql)
            if self.command == "POST":
                self._cached_post_body = self._read_post_body()
            else:
                self._cached_post_body = None

            if not role:
                if parsed.path.startswith("/api/"):
                    self.send_response(HTTPStatus.UNAUTHORIZED)
                    self.end_headers()
                    return
                self._send_html(self._layout("Login", "login", self._login_form(), role=None), HTTPStatus.UNAUTHORIZED)
                return

            try:
                if parsed.path == "/":
                    self._dashboard(params, role)
                elif parsed.path == "/items":
                    self._items(params, role)
                elif parsed.path == "/logs":
                    self._logs(params, role)
                elif parsed.path == "/trace":
                    self._trace(params, role)
                elif parsed.path == "/codes":
                    self._codes(params, role)
                elif parsed.path == "/tables":
                    self._tables(params, role)
                elif parsed.path == "/table":
                    self._table(params, role)
                elif parsed.path == "/sql":
                    if not _has_perm(role, 'run_sql'):
                        self._send_html(self._layout("Forbidden", "", "<div class='login-box'><h1>403 Forbidden</h1><p>You do not have permission to access SQL.</p></div>", role=role), HTTPStatus.FORBIDDEN)
                        return
                    self._sql(params, role)
                elif parsed.path == "/users":
                    if not _has_perm(role, 'manage_users'):
                        self._send_html(self._layout("Forbidden", "", "<div class='login-box'><h1>403 Forbidden</h1></div>", role=role), HTTPStatus.FORBIDDEN)
                        return
                    self._users_page(params, role)
                elif parsed.path == "/action/remove" and self.command == "POST":
                    self._action_remove(role)
                elif parsed.path == "/action/add" and self.command == "POST":
                    self._action_add(role)
                elif parsed.path == "/action/toggle_code" and self.command == "POST":
                    self._action_toggle_code(role)
                elif parsed.path == "/action/add_user" and self.command == "POST":
                    self._action_add_user(role)
                elif parsed.path == "/action/remove_user" and self.command == "POST":
                    self._action_remove_user(role)
                elif parsed.path == "/action/change_role" and self.command == "POST":
                    self._action_change_role(role)
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
                    self._send_html(self._layout("Not found", "", "<div class='login-box'><h1>Not found</h1></div>", role=role), HTTPStatus.NOT_FOUND)
            except Exception as exc:
                body = f"<div class='login-box'><h1>Server error</h1><pre class='error'>{h(exc)}</pre></div>"
                self._send_html(self._layout("Error", "", body, role=role), HTTPStatus.INTERNAL_SERVER_ERROR)

        def _log_auth_attempt(self, method: str, identifier: str, success: bool, role: str | None = None) -> None:
            """Log authentication attempts to database."""
            ip = self.client_address[0] if hasattr(self, "client_address") else "unknown"
            try:
                with self._connect_write() as conn:
                    conn.execute(
                        '''CREATE TABLE IF NOT EXISTS auth_logs (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                            method TEXT,
                            identifier TEXT,
                            success INTEGER,
                            role TEXT,
                            ip_address TEXT
                        )'''
                    )
                    conn.execute(
                        "INSERT INTO auth_logs (method, identifier, success, role, ip_address) VALUES (?, ?, ?, ?, ?)",
                        (method, identifier, 1 if success else 0, role or "", ip)
                    )
            except Exception as e:
                self.log_message(f"Failed to write to auth_logs: {e}")

        def _handle_login(self) -> None:
            """Authenticate token via POST, set HttpOnly session cookie, redirect to dashboard."""
            posted = self._read_post_body()
            token = posted.get("token", [""])[0].strip()
            role = token_to_role.get(token)

            if not role:
                self._log_auth_attempt("token", token, False)
                body = self._login_form(error=True)
                self._send_html(self._layout("Login", "login", body, role=None), HTTPStatus.UNAUTHORIZED)
                return

            self._log_auth_attempt("token", token, True, role)
            cookie_value = _sign_cookie(role, session_secret)
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", f"{COOKIE_NAME}={cookie_value}; HttpOnly; SameSite=Lax; Path=/; Max-Age={COOKIE_MAX_AGE}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _handle_logout(self) -> None:
            """Clear session cookie and redirect to login."""
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", f"{COOKIE_NAME}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _handle_google_auth_redirect(self) -> None:
            """Redirect user to Google's OAuth consent screen."""
            if not oauth_enabled:
                self._send_html(self._layout("Error", "", "<div class='login-box'><h1>Google OAuth not configured</h1></div>", role=None))
                return
            redirect_uri = _build_redirect_uri(self.headers.get("Host", ""))
            state = secrets.token_urlsafe(32)
            google_url = (
                "https://accounts.google.com/o/oauth2/v2/auth?"
                + urllib.parse.urlencode({
                    "client_id": oauth_client_id,
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
                    "scope": "openid email",
                    "state": state,
                    "access_type": "online",
                    "prompt": "select_account",
                })
            )
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", google_url)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _handle_google_callback(self, params: dict[str, str]) -> None:
            """Exchange Google auth code for id_token, verify email, set session cookie."""
            code = params.get("code", "")
            if not code:
                self._send_html(self._layout("Error", "", "<div class='login-box'><h1>Missing auth code</h1><p>Google did not return an authorization code.</p></div>", role=None))
                return
            redirect_uri = _build_redirect_uri(self.headers.get("Host", ""))
            # Exchange code for tokens
            try:
                import urllib.request as urlreq
                token_data = urllib.parse.urlencode({
                    "code": code,
                    "client_id": oauth_client_id,
                    "client_secret": oauth_client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                }).encode()
                req = urlreq.Request("https://oauth2.googleapis.com/token", data=token_data, method="POST")
                req.add_header("Content-Type", "application/x-www-form-urlencoded")
                with urlreq.urlopen(req, timeout=10) as resp:
                    token_resp = json.loads(resp.read().decode())
            except Exception as exc:
                self._send_html(self._layout("Error", "", f"<div class='login-box'><h1>Token exchange failed</h1><pre class='error'>{h(exc)}</pre></div>", role=None))
                return

            # Decode id_token JWT payload (no verification needed — received directly from Google over HTTPS)
            id_token = token_resp.get("id_token", "")
            try:
                payload_b64 = id_token.split(".")[1]
                # Fix base64 padding
                payload_b64 += "=" * (4 - len(payload_b64) % 4)
                payload = json.loads(base64.urlsafe_b64decode(payload_b64))
                email = payload.get("email", "").lower()
            except Exception:
                self._send_html(self._layout("Error", "", "<div class='login-box'><h1>Could not read Google profile</h1></div>", role=None))
                return

            # Check email against allowed lists
            role = oauth_email_to_role.get(email)

            if not role:
                self._log_auth_attempt("google", email, False)
                self._send_html(self._layout("Access Denied", "",
                    f"<div class='login-box'><h1>Access Denied</h1>"
                    f"<p style='margin-top:16px;'>The account <strong>{h(email)}</strong> is not authorized.</p>"
                    f"<p class='muted' style='margin-top:12px;'>Contact the system administrator to request access.</p>"
                    f"<a href='/logout' class='btn-primary' style='display:block;text-align:center;margin-top:24px;text-decoration:none;'>Back to Login</a></div>",
                    role=None), HTTPStatus.FORBIDDEN)
                return

            self._log_auth_attempt("google", email, True, role)
            cookie_value = _sign_cookie(role, session_secret)
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", f"{COOKIE_NAME}={cookie_value}; HttpOnly; SameSite=Lax; Path=/; Max-Age={COOKIE_MAX_AGE}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _connect(self):
            return aq.connect_readonly(db_path)

        def _connect_write(self):
            return aq.connect_writable(db_path)

        def _redirect_with_msg(self, path: str, msg: str, is_error: bool = False) -> None:
            sep = "&" if "?" in path else "?"
            key = "error" if is_error else "success"
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", f"{path}{sep}{key}={urllib.parse.quote(msg)}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _action_remove(self, role: str) -> None:
            if not _has_perm(role, "manual_remove"):
                self.send_response(HTTPStatus.FORBIDDEN); self.end_headers(); return
            posted = self._cached_post_body or {}
            uid = posted.get("uid", [""])[0].strip()
            try:
                with self._connect_write() as conn:
                    msg = aq.manual_remove_item(conn, uid)
                self._redirect_with_msg("/items", msg)
            except Exception as exc:
                self._redirect_with_msg("/items", str(exc), is_error=True)

        def _action_add(self, role: str) -> None:
            if not _has_perm(role, "manual_add"):
                self.send_response(HTTPStatus.FORBIDDEN); self.end_headers(); return
            posted = self._cached_post_body or {}
            uid = posted.get("uid", [""])[0].strip()
            item_class = posted.get("item_class", [""])[0].strip()
            # If only uid given, try to resolve class from registered_codes or items
            if uid and not item_class:
                with self._connect() as conn:
                    row = conn.execute("SELECT item_class FROM items WHERE uid = ?", (uid,)).fetchone()
                    if row:
                        item_class = row["item_class"]
                    elif aq.table_exists(conn, "registered_codes"):
                        row = conn.execute(
                            "SELECT item_class FROM registered_codes WHERE public_uid = ? OR item_class || '_' || public_uid = ?",
                            (uid, uid)
                        ).fetchone()
                        if row:
                            item_class = row["item_class"]
            if not item_class:
                self._redirect_with_msg("/items", "Could not resolve item class for this UID. Please provide it manually.", is_error=True)
                return
            try:
                with self._connect_write() as conn:
                    msg = aq.manual_add_item(conn, uid, item_class)
                self._redirect_with_msg("/items", msg)
            except Exception as exc:
                self._redirect_with_msg("/items", str(exc), is_error=True)

        def _action_toggle_code(self, role: str) -> None:
            if not _has_perm(role, "manage_codes"):
                self.send_response(HTTPStatus.FORBIDDEN); self.end_headers(); return
            posted = self._cached_post_body or {}
            public_uid = posted.get("public_uid", [""])[0].strip()
            try:
                with self._connect_write() as conn:
                    msg = aq.toggle_code_active(conn, public_uid)
                self._redirect_with_msg("/codes", msg)
            except Exception as exc:
                self._redirect_with_msg("/codes", str(exc), is_error=True)

        def _action_add_user(self, role: str) -> None:
            if not _has_perm(role, "manage_users"):
                self.send_response(HTTPStatus.FORBIDDEN); self.end_headers(); return
            posted = self._cached_post_body or {}
            auth_type = posted.get("auth_type", ["token"])[0]
            new_role = posted.get("role", ["viewer"])[0]
            if new_role not in roles_def:
                self._redirect_with_msg("/users", f"Unknown role: {new_role}", is_error=True); return
            try:
                cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
                wc = cfg.get("web_admin", {})
                if auth_type == "google":
                    email = posted.get("email", [""])[0].strip().lower()
                    if not email:
                        self._redirect_with_msg("/users", "Email is required.", is_error=True); return
                    oauth = wc.setdefault("google_oauth", {})
                    users_list = oauth.setdefault("users", [])
                    if any(u.get("email", "").lower() == email for u in users_list):
                        self._redirect_with_msg("/users", f"Email {email} already exists.", is_error=True); return
                    users_list.append({"email": email, "role": new_role})
                else:
                    token = posted.get("token", [""])[0].strip()
                    if not token:
                        self._redirect_with_msg("/users", "Token is required.", is_error=True); return
                    users_list = wc.setdefault("users", [])
                    if any(u.get("token") == token for u in users_list):
                        self._redirect_with_msg("/users", "Token already exists.", is_error=True); return
                    users_list.append({"token": token, "role": new_role})
                _save_config(wc)
                # Reload in-memory state
                nonlocal token_to_role, oauth_email_to_role, token_users, oauth_users
                token_users = wc.get("users", [])
                token_to_role = {u["token"]: u["role"] for u in token_users if u.get("token") and u.get("role")}
                oauth_users = wc.get("google_oauth", {}).get("users", [])
                oauth_email_to_role = {u["email"].lower(): u["role"] for u in oauth_users if u.get("email") and u.get("role")}
                self._redirect_with_msg("/users", f"User added with role '{new_role}'.")
            except Exception as exc:
                self._redirect_with_msg("/users", str(exc), is_error=True)

        def _action_remove_user(self, role: str) -> None:
            if not _has_perm(role, "manage_users"):
                self.send_response(HTTPStatus.FORBIDDEN); self.end_headers(); return
            posted = self._cached_post_body or {}
            user_id = posted.get("user_id", [""])[0].strip()
            user_type = posted.get("user_type", ["token"])[0]
            try:
                cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
                wc = cfg.get("web_admin", {})
                if user_type == "google":
                    users_list = wc.get("google_oauth", {}).get("users", [])
                    wc["google_oauth"]["users"] = [u for u in users_list if u.get("email", "").lower() != user_id.lower()]
                else:
                    users_list = wc.get("users", [])
                    wc["users"] = [u for u in users_list if u.get("token") != user_id]
                _save_config(wc)
                nonlocal token_to_role, oauth_email_to_role, token_users, oauth_users
                token_users = wc.get("users", [])
                token_to_role = {u["token"]: u["role"] for u in token_users if u.get("token") and u.get("role")}
                oauth_users = wc.get("google_oauth", {}).get("users", [])
                oauth_email_to_role = {u["email"].lower(): u["role"] for u in oauth_users if u.get("email") and u.get("role")}
                self._redirect_with_msg("/users", "User removed.")
            except Exception as exc:
                self._redirect_with_msg("/users", str(exc), is_error=True)

        def _action_change_role(self, role: str) -> None:
            if not _has_perm(role, "manage_users"):
                self.send_response(HTTPStatus.FORBIDDEN); self.end_headers(); return
            posted = self._cached_post_body or {}
            user_id = posted.get("user_id", [""])[0].strip()
            user_type = posted.get("user_type", ["token"])[0]
            new_role = posted.get("role", [""])[0]
            if new_role not in roles_def:
                self._redirect_with_msg("/users", f"Unknown role: {new_role}", is_error=True); return
            try:
                cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
                wc = cfg.get("web_admin", {})
                if user_type == "google":
                    for u in wc.get("google_oauth", {}).get("users", []):
                        if u.get("email", "").lower() == user_id.lower():
                            u["role"] = new_role
                else:
                    for u in wc.get("users", []):
                        if u.get("token") == user_id:
                            u["role"] = new_role
                _save_config(wc)
                nonlocal token_to_role, oauth_email_to_role, token_users, oauth_users
                token_users = wc.get("users", [])
                token_to_role = {u["token"]: u["role"] for u in token_users if u.get("token") and u.get("role")}
                oauth_users = wc.get("google_oauth", {}).get("users", [])
                oauth_email_to_role = {u["email"].lower(): u["role"] for u in oauth_users if u.get("email") and u.get("role")}
                self._redirect_with_msg("/users", f"Role changed to '{new_role}'.")
            except Exception as exc:
                self._redirect_with_msg("/users", str(exc), is_error=True)

        def _dashboard(self, params: dict[str, str], role: str) -> None:
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
                else "<p class='muted' style='padding:16px;'>No obvious anomalies in the current checks.</p>"
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
            self._send_html(self._layout("Dashboard", "dashboard", body, role=role))

        def _items(self, params: dict[str, str], role: str) -> None:
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
                codes_list = aq.get_registered_codes_list(conn) if _has_perm(role, "manual_add") else []

            # Toast notification
            toast = ""
            if params.get("success"):
                toast = f"<div class='toast success'>{h(params['success'])}</div>"
            elif params.get("error"):
                toast = f"<div class='toast error'>{h(params['error'])}</div>"

            # Manual add form
            add_form = ""
            if _has_perm(role, "manual_add"):
                options = "".join(f'<option value="{h(c["public_uid"])}" data-class="{h(c["item_class"])}">{h(c["item_class"])} — {h(c["public_uid"])}</option>' for c in codes_list)
                add_form = f"""
                <details class="action-panel">
                  <summary>+ Manual Add Item</summary>
                  <form method="post" action="/action/add" class="action-form">
                    <div class="input-group" style="flex:1;">
                      <label>Select registered code</label>
                      <select name="uid" id="add-uid-select" onchange="var o=this.options[this.selectedIndex]; document.getElementById('add-class').value=o.dataset.class||'';">
                        <option value="">— or type UID below —</option>
                        {options}
                      </select>
                    </div>
                    <div class="input-group" style="flex:1;">
                      <label>Or enter UID manually</label>
                      <input name="uid" placeholder="UID" id="add-uid-manual">
                    </div>
                    <div class="input-group" style="flex:0.6;">
                      <label>Class (auto-filled)</label>
                      <input name="item_class" id="add-class" placeholder="Auto" readonly>
                    </div>
                    <button type="submit" class="btn-action add" onclick="return confirm('Add this item to inventory?')">Add</button>
                  </form>
                </details>
                """

            # Build table with action column
            can_remove = _has_perm(role, "manual_remove")
            can_add = _has_perm(role, "manual_add")
            rows_html = self._items_table_with_actions(result["rows"], result["columns"], role, can_remove, can_add)

            body = f"""
            {toast}
            <div class="section-head"><h1>Inventory</h1><span class="status neutral">{result['total']} rows</span></div>
            {add_form}
            {self._item_filters(params)}
            {rows_html}
            """
            self._send_html(self._layout("Inventory", "items", body, role=role))

        def _items_table_with_actions(self, rows, columns, role, can_remove, can_add):
            """Render inventory table with action buttons per row."""
            if not columns or not rows:
                return "<p class='muted' style='padding:16px;'>No rows.</p>"
            cols = list(columns)
            show_actions = can_remove or can_add
            head = "".join(f"<th>{h(col)}</th>" for col in cols)
            if show_actions:
                head += "<th>Actions</th>"
            body_rows = []
            for row in rows:
                cells = []
                for col in cols:
                    value = row.get(col)
                    cell = h(value)
                    if col == "uid" and value:
                        cell = f'<a href="{self._url("/trace", uid=value)}">{h(value)}</a>'
                    if col == "in_stock":
                        cell = '<span class="status in">IN</span>' if value == 1 else '<span class="status out">OUT</span>'
                    cells.append(f"<td>{cell}</td>")
                if show_actions:
                    uid = row.get("uid", "")
                    in_stock = row.get("in_stock")
                    btn = ""
                    if in_stock == 1 and can_remove:
                        btn = f'<form method="post" action="/action/remove" style="display:inline;"><input type="hidden" name="uid" value="{h(uid)}"><button type="submit" class="btn-action remove" onclick="return confirm(\'Remove {h(uid)}?\')">Remove</button></form>'
                    elif in_stock == 0 and can_add:
                        btn = f'<form method="post" action="/action/add" style="display:inline;"><input type="hidden" name="uid" value="{h(uid)}"><button type="submit" class="btn-action add" onclick="return confirm(\'Add back {h(uid)}?\')">Add back</button></form>'
                    cells.append(f"<td>{btn}</td>")
                body_rows.append("<tr>" + "".join(cells) + "</tr>")
            return f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table></div>"

        def _logs(self, params: dict[str, str], role: str) -> None:
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
            <div class="section-head">
              <div style="display:flex; align-items:center; gap:12px;">
                <h1>Movements</h1>
                <span class="status neutral">{result['total']} matching</span>
              </div>
              <a href="{export_url}">Export CSV</a>
            </div>
            {self._log_filters(params)}
            {table_html(result["rows"], result["columns"], trace_links=True, url=self._url)}
            """
            self._send_html(self._layout("Movements", "logs", body, role=role))

        def _trace(self, params: dict[str, str], role: str) -> None:
            uid = (params.get("uid") or "").strip()
            form = f"""
            <form class="filters" method="get" action="/trace">
              <div style="flex-grow:1;">
                <label>UID Trace Query</label>
                <input name="uid" value="{h(uid)}" placeholder="Search by UID..." style="width:100%;">
              </div>
              <button type="submit" class="btn-primary" style="width:auto;">Trace</button>
            </form>
            """
            if not uid:
                body = f"<div class='section-head'><h1>Trace item</h1></div>{form}"
            else:
                with self._connect() as conn:
                    trace = aq.trace_uid(conn, uid)
                body = f"""
                <div class="section-head"><h1>Trace</h1><span class="status {trace['current_status']}">{h(trace['current_status'].upper())}</span></div>
                {form}
                <p class="muted" style="margin-bottom:20px;">Candidates found: {h(', '.join(trace['candidate_uids']) or 'None')}</p>
                <section><h2>Item</h2>{table_html(trace['items'], columns_from_rows(trace['items']))}</section>
                <section><h2>Registered code</h2>{table_html(trace['registered_codes'], columns_from_rows(trace['registered_codes']))}</section>
                <section><h2>Timeline</h2>{table_html(trace['logs'], columns_from_rows(trace['logs'], ['id','uid','item_class','action','timestamp']))}</section>
                <section><h2>Trace checks</h2>{table_html(trace['anomalies'], ['uid','action','timestamp','detail']) if trace['anomalies'] else "<p class='muted' style='padding:16px;'>No trace anomalies.</p>"}</section>
                """
            self._send_html(self._layout("Trace", "trace", body, role=role))

        def _codes(self, params: dict[str, str], role: str) -> None:
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

            toast = ""
            if params.get("success"):
                toast = f"<div class='toast success'>{h(params['success'])}</div>"
            elif params.get("error"):
                toast = f"<div class='toast error'>{h(params['error'])}</div>"

            can_manage = _has_perm(role, "manage_codes")
            rows_html = self._codes_table_with_actions(result["rows"], result["columns"], can_manage)

            body = f"""
            {toast}
            <div class="section-head"><h1>Registered codes</h1><span class="status neutral">{result['total']} rows</span></div>
            {self._code_filters(params)}
            {rows_html}
            """
            self._send_html(self._layout("Codes", "codes", body, role=role))

        def _codes_table_with_actions(self, rows, columns, can_manage):
            if not columns or not rows:
                return "<p class='muted' style='padding:16px;'>No rows.</p>"
            cols = list(columns)
            head = "".join(f"<th>{h(col)}</th>" for col in cols)
            if can_manage:
                head += "<th>Actions</th>"
            body_rows = []
            for row in rows:
                cells = []
                for col in cols:
                    value = row.get(col)
                    cell = h(value)
                    if col == "active":
                        cell = '<span class="status in">YES</span>' if value == 1 else '<span class="status out">NO</span>'
                    if col == "public_uid" and value:
                        cell = f'<a href="{self._url("/trace", uid=value)}">{h(value)}</a>'
                    cells.append(f"<td>{cell}</td>")
                if can_manage:
                    puid = row.get("public_uid", "")
                    is_active = row.get("active", 0)
                    label = "Deactivate" if is_active == 1 else "Activate"
                    cls = "remove" if is_active == 1 else "add"
                    btn = f'<form method="post" action="/action/toggle_code" style="display:inline;"><input type="hidden" name="public_uid" value="{h(puid)}"><button type="submit" class="btn-action {cls}" onclick="return confirm(\'{label} {h(puid)}?\')">{label}</button></form>'
                    cells.append(f"<td>{btn}</td>")
                body_rows.append("<tr>" + "".join(cells) + "</tr>")
            return f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table></div>"

        def _tables(self, params: dict[str, str], role: str) -> None:
            with self._connect() as conn:
                rows = [
                    {"table": name, "rows": aq.count_rows(conn, name)}
                    for name in aq.table_names(conn)
                ]
            body = f"""
            <div class="section-head"><h1>Tables</h1></div>
            {table_html(rows, ["table", "rows"], table_links=True, url=self._url)}
            """
            self._send_html(self._layout("Tables", "tables", body, role=role))

        def _table(self, params: dict[str, str], role: str) -> None:
            table = params.get("name") or ""
            if not table:
                self._send_html(self._layout("Table", "tables", "<div class='login-box'><h1>Missing table name</h1></div>", role=role), HTTPStatus.BAD_REQUEST)
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
            <div class="section-head">
              <div style="display:flex; align-items:center; gap:12px;">
                <h1>{h(table)}</h1>
                <span class="status neutral">{result['total']} rows</span>
              </div>
              <a href="{export_url}">Export CSV</a>
            </div>
            </div>
            {self._table_filters(table, params)}
            <section><h2>Rows</h2>{table_html(
                result["rows"], 
                result["columns"], 
                trace_links=True, 
                url=self._url, 
                table_name=table, 
                can_manage_users=_has_perm(role, "manage_users"),
                existing_users={"google": oauth_email_to_role, "token": token_to_role}
            )}</section>
            <section><h2>Schema</h2>{table_html(schema, ["cid","name","type","notnull","dflt_value","pk"])}</section>
            """
            
            if table == "auth_logs" and _has_perm(role, "manage_users"):
                role_options = "".join(f'<option value="{h(rn)}">{h(rn)}</option>' for rn in roles_def.keys())
                body += f"""
                <dialog id="userModal" style="border:1px solid var(--line); border-radius:var(--radius); padding:24px; box-shadow:var(--shadow); width:100%; max-width:400px; background:var(--surface);">
                  <form method="dialog" style="float:right; margin-top:-10px; margin-right:-10px;">
                    <button style="border:none;background:none;font-size:20px;cursor:pointer;">✕</button>
                  </form>
                  <h3 style="margin-top:0;" id="userModalTitle">Modify User</h3>
                  <div id="userModalCurrentRole" style="margin-bottom:16px;" class="muted"></div>
                  
                  <form id="userModalForm" method="post" action="/action/change_role">
                    <input type="hidden" name="auth_type" id="userModalAuthType">
                    <input type="hidden" name="user_type" id="userModalUserType">
                    <input type="hidden" name="user_id" id="userModalUserId">
                    <input type="hidden" name="email" id="userModalEmail">
                    <input type="hidden" name="token" id="userModalToken">
                    
                    <div class="input-group">
                      <label>Assign Role</label>
                      <select name="role" id="userModalRoleSelect" style="width:100%; padding:8px; border:1px solid var(--line); border-radius:4px;">
                        {role_options}
                      </select>
                    </div>
                    
                    <div style="display:flex;gap:12px;margin-top:24px;">
                      <button type="submit" class="btn-primary" id="userModalSaveBtn" style="flex:1;">Save</button>
                      <button type="button" class="btn-action remove" id="userModalRemoveBtn" style="flex:1; justify-content:center;" onclick="document.getElementById('userModalForm').action='/action/remove_user'; document.getElementById('userModalForm').submit();">Remove Access</button>
                    </div>
                  </form>
                </dialog>
                <script>
                  function openUserModal(action, method, identifier, currentRole) {{
                      const dialog = document.getElementById('userModal');
                      const form = document.getElementById('userModalForm');
                      
                      document.getElementById('userModalAuthType').value = method;
                      document.getElementById('userModalUserType').value = method;
                      document.getElementById('userModalUserId').value = identifier;
                      document.getElementById('userModalEmail').value = identifier;
                      document.getElementById('userModalToken').value = identifier;
                      
                      const roleText = document.getElementById('userModalCurrentRole');
                      const removeBtn = document.getElementById('userModalRemoveBtn');
                      const title = document.getElementById('userModalTitle');
                      const roleSelect = document.getElementById('userModalRoleSelect');
                      
                      if (action === 'add') {{
                          title.innerText = "Add: " + identifier;
                          form.action = '/action/add_user';
                          roleText.innerText = "Not in system.";
                          removeBtn.style.display = 'none';
                          roleSelect.value = 'viewer';
                      }} else {{
                          title.innerText = "Modify: " + identifier;
                          form.action = '/action/change_role';
                          roleText.innerText = "Current role: " + currentRole;
                          roleSelect.value = currentRole;
                          removeBtn.style.display = 'flex';
                      }}
                      dialog.showModal();
                  }}
                </script>
                """

            self._send_html(self._layout("Table", "tables", body, role=role))

        def _sql(self, params: dict[str, str], role: str) -> None:
            if self.command == "POST" and self._cached_post_body:
                sql = self._cached_post_body.get("sql", [""])[0]
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
                        result_html = "<p class='muted' style='margin-bottom:10px;'>Result truncated to 300 rows.</p>" + result_html
                except Exception as exc:
                    error_html = f"<div class='metric error' style='margin-bottom:16px;'>{h(exc)}</div>"

            body = f"""
            <div class="section-head"><h1>SQL Terminal</h1><span class="status neutral">SELECT Only</span></div>
            <form class="sql-form" method="post" action="/sql">
              <textarea name="sql" spellcheck="false" placeholder="Enter your SQL query here...">{h(sql)}</textarea>
              <button type="submit" class="btn-primary" style="width:120px;">Run Query</button>
            </form>
            <div style="margin-top:24px;">
                {error_html}
                {result_html}
            </div>
            """
            self._send_html(self._layout("SQL", "sql", body, role=role))

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

        def _users_page(self, params: dict[str, str], role: str) -> None:
            toast = ""
            if params.get("success"):
                toast = f"<div class='toast success'>{h(params['success'])}</div>"
            elif params.get("error"):
                toast = f"<div class='toast error'>{h(params['error'])}</div>"

            role_options = "".join(f'<option value="{h(r)}">{h(r)}</option>' for r in roles_def.keys())

            # Token users table
            token_rows = ""
            for u in token_users:
                t = u.get("token", "")
                r = u.get("role", "")
                masked = t[:4] + "•" * max(0, len(t) - 4) if len(t) > 4 else "••••"
                role_select = "".join(
                    f'<option value="{h(rn)}" {"selected" if rn == r else ""}>{h(rn)}</option>'
                    for rn in roles_def.keys()
                )
                token_rows += f"""<tr>
                  <td><code>{h(masked)}</code></td>
                  <td><span class="status neutral">{h(r)}</span></td>
                  <td>
                    <form method="post" action="/action/change_role" style="display:inline-flex;gap:6px;align-items:center;">
                      <input type="hidden" name="user_id" value="{h(t)}">
                      <input type="hidden" name="user_type" value="token">
                      <select name="role" class="inline-select">{role_select}</select>
                      <button type="submit" class="btn-action neutral">Save</button>
                    </form>
                    <form method="post" action="/action/remove_user" style="display:inline;margin-left:4px;">
                      <input type="hidden" name="user_id" value="{h(t)}">
                      <input type="hidden" name="user_type" value="token">
                      <button type="submit" class="btn-action remove" onclick="return confirm('Remove this user?')">✕</button>
                    </form>
                  </td>
                </tr>"""

            # Google users table
            google_rows = ""
            for u in oauth_users:
                e = u.get("email", "")
                r = u.get("role", "")
                role_select = "".join(
                    f'<option value="{h(rn)}" {"selected" if rn == r else ""}>{h(rn)}</option>'
                    for rn in roles_def.keys()
                )
                google_rows += f"""<tr>
                  <td>{h(e)}</td>
                  <td><span class="status neutral">{h(r)}</span></td>
                  <td>
                    <form method="post" action="/action/change_role" style="display:inline-flex;gap:6px;align-items:center;">
                      <input type="hidden" name="user_id" value="{h(e)}">
                      <input type="hidden" name="user_type" value="google">
                      <select name="role" class="inline-select">{role_select}</select>
                      <button type="submit" class="btn-action neutral">Save</button>
                    </form>
                    <form method="post" action="/action/remove_user" style="display:inline;margin-left:4px;">
                      <input type="hidden" name="user_id" value="{h(e)}">
                      <input type="hidden" name="user_type" value="google">
                      <button type="submit" class="btn-action remove" onclick="return confirm('Remove {h(e)}?')">✕</button>
                    </form>
                  </td>
                </tr>"""

            # Roles summary
            roles_summary = ""
            for rname, rdef in roles_def.items():
                perms = rdef.get("permissions", [])
                perms_str = ", ".join(perms) if perms else "none"
                roles_summary += f"<tr><td><strong>{h(rname)}</strong></td><td class='muted' style='font-size:12px;'>{h(perms_str)}</td></tr>"

            body = f"""
            {toast}
            <div class="section-head"><h1>User Management</h1></div>

            <section>
              <h2>Roles & Permissions</h2>
              <div class="table-wrap"><table><thead><tr><th>Role</th><th>Permissions</th></tr></thead><tbody>{roles_summary}</tbody></table></div>
            </section>

            <section>
              <h2>Token Users</h2>
              <div class="table-wrap"><table><thead><tr><th>Token</th><th>Role</th><th>Actions</th></tr></thead><tbody>{token_rows if token_rows else '<tr><td colspan="3" class="muted" style="text-align:center;padding:16px;">No token users.</td></tr>'}</tbody></table></div>
            </section>

            <section>
              <h2>Google Users</h2>
              <div class="table-wrap"><table><thead><tr><th>Email</th><th>Role</th><th>Actions</th></tr></thead><tbody>{google_rows if google_rows else '<tr><td colspan="3" class="muted" style="text-align:center;padding:16px;">No Google users.</td></tr>'}</tbody></table></div>
            </section>

            <section>
              <h2>Add User</h2>
              <div class="card" style="padding:20px;">
                <form method="post" action="/action/add_user" class="action-form">
                  <div class="input-group">
                    <label>Type</label>
                    <select name="auth_type" id="auth-type-select" onchange="document.getElementById('token-field').style.display=this.value==='token'?'block':'none'; document.getElementById('email-field').style.display=this.value==='google'?'block':'none';">
                      <option value="token">Token</option>
                      <option value="google">Google Email</option>
                    </select>
                  </div>
                  <div class="input-group" id="token-field">
                    <label>Token</label>
                    <input name="token" placeholder="Enter access token">
                  </div>
                  <div class="input-group" id="email-field" style="display:none;">
                    <label>Email</label>
                    <input name="email" type="email" placeholder="user@gmail.com">
                  </div>
                  <div class="input-group">
                    <label>Role</label>
                    <select name="role">{role_options}</select>
                  </div>
                  <button type="submit" class="btn-primary" style="width:auto;">Add User</button>
                </form>
              </div>
            </section>
            """
            self._send_html(self._layout("Users", "users", body, role=role))

        def _layout(self, title: str, active: str, body: str, role: str | None) -> str:
            nav_items = [
                ("dashboard", "/", "Dashboard"),
                ("items", "/items", "Inventory"),
                ("logs", "/logs", "Movements"),
                ("trace", "/trace", "Trace"),
                ("codes", "/codes", "Codes"),
                ("tables", "/tables", "Tables"),
            ]
            if role and _has_perm(role, "run_sql"):
                nav_items.append(("sql", "/sql", "SQL"))
            if role and _has_perm(role, "manage_users"):
                nav_items.append(("users", "/users", "Users"))
                
            nav = "".join(
                f"<a class=\"{'active' if key == active else ''}\" href=\"{path}\">{label}</a>"
                for key, path, label in nav_items
            )
            logout_btn = '<a href="/logout" class="logout-btn">Sign Out</a>' if role else ''
            nav_html = f"<nav>{nav}{logout_btn}</nav>" if role else ""
            role_indicator = f"<span class='status neutral'>{role.upper()}</span>" if role else ""
            
            return f"""<!doctype html>
            <html lang="en">
            <head>
              <meta charset="utf-8">
              <meta name="viewport" content="width=device-width, initial-scale=1">
              <title>{h(title)} - Smart Checkout</title>
              <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
              <style>{CSS}</style>
            </head>
            <body>
              <header>
                <div class="brand">
                  <div style="display:flex; align-items:center; gap:8px;">
                      <strong>Smart Checkout</strong>
                      {role_indicator}
                  </div>
                  <span>{h(str(db_path))}</span>
                </div>
                {nav_html}
              </header>
              <main>{body}</main>
            </body>
            </html>"""

        def _url(self, path: str, **params: Any) -> str:
            clean = {k: v for k, v in params.items() if v not in (None, "")}
            query = urllib.parse.urlencode(clean, doseq=True)
            return path + (f"?{query}" if query else "")

        def _login_form(self, error: bool = False) -> str:
            error_msg = "<p class='error' style='text-align:center; margin-bottom:20px; font-size:13px;'>Invalid token. Please try again.</p>" if error else ""
            is_local = self._is_local_request()

            # Token form (shown for local requests)
            token_section = ""
            if is_local:
                token_section = f"""
                {error_msg}
                <form method="post" action="/login">
                  <div class="input-group">
                    <label>Access Token</label>
                    <input name="token" type="password" placeholder="Enter your secure token" autofocus required>
                  </div>
                  <button type="submit" class="btn-primary">Sign In</button>
                </form>
                """

            # Google OAuth button (shown for remote requests, or both if OAuth is configured)
            google_section = ""
            if oauth_enabled:
                if is_local:
                    google_section = """
                    <div style="text-align:center; margin-top:24px; padding-top:24px; border-top:1px solid var(--line);">
                      <p class="muted" style="margin-bottom:12px; font-size:13px;">Or sign in with</p>
                      <a href="/auth/google" class="google-btn">
                        <svg width="18" height="18" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>
                        Google
                      </a>
                    </div>
                    """
                else:
                    google_section = f"""
                    <a href="/auth/google" class="google-btn" style="margin-top:8px;">
                      <svg width="18" height="18" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>
                      Sign in with Google
                    </a>
                    """

            # Remote but no OAuth = show a message
            if not is_local and not oauth_enabled:
                google_section = "<p class='muted' style='text-align:center;'>Remote access requires Google OAuth to be configured.</p>"

            return f"""
            <div class="login-container">
              <div class="login-box">
                <div class="login-header">
                  <h2>Smart Checkout Admin</h2>
                  <p>{'Sign in to access the portal' if is_local else 'Sign in with your Google account'}</p>
                </div>
                {token_section}
                {google_section}
              </div>
            </div>
            """

        def _log_filters(self, params: dict[str, str]) -> str:
            return f"""
            <form class="filters" method="get" action="{self._url('/logs')}">
              <div style="display:grid; gap:6px;">
                  <label>Search</label>
                  <input name="search" value="{h(params.get('search',''))}">
              </div>
              <div style="display:grid; gap:6px;">
                  <label>UID</label>
                  <input name="uid" value="{h(params.get('uid',''))}">
              </div>
              <div style="display:grid; gap:6px;">
                  <label>Class</label>
                  <input name="class" value="{h(params.get('class',''))}">
              </div>
              <div style="display:grid; gap:6px;">
                  <label>Action</label>
                  <select name="action">{options(['','ADDED','REMOVED'], params.get('action',''))}</select>
              </div>
              <div style="display:grid; gap:6px;">
                  <label>From</label>
                  <input type="date" name="from" value="{h(params.get('from',''))}">
              </div>
              <div style="display:grid; gap:6px;">
                  <label>To</label>
                  <input type="date" name="to" value="{h(params.get('to',''))}">
              </div>
              <div style="display:grid; gap:6px;">
                  <label>Sort</label>
                  <input name="sort" value="{h(params.get('sort','timestamp'))}">
              </div>
              <div style="display:grid; gap:6px;">
                  <label>Order</label>
                  <select name="order">{options(['desc','asc'], params.get('order','desc'))}</select>
              </div>
              <button type="submit" class="btn-primary" style="width:100px;">Apply</button>
            </form>
            """

        def _item_filters(self, params: dict[str, str]) -> str:
            return f"""
            <form class="filters" method="get" action="{self._url('/items')}">
              <div style="display:grid; gap:6px;">
                  <label>Search</label>
                  <input name="search" value="{h(params.get('search',''))}">
              </div>
              <div style="display:grid; gap:6px;">
                  <label>Class</label>
                  <input name="class" value="{h(params.get('class',''))}">
              </div>
              <div style="display:grid; gap:6px;">
                  <label>Status</label>
                  <select name="in_stock">{options(['','yes','no'], params.get('in_stock',''))}</select>
              </div>
              <div style="display:grid; gap:6px;">
                  <label>Sort</label>
                  <select name="sort">{options(['uid','item_class','in_stock','last_seen','movement_count'], params.get('sort','uid'))}</select>
              </div>
              <div style="display:grid; gap:6px;">
                  <label>Order</label>
                  <select name="order">{options(['asc','desc'], params.get('order','asc'))}</select>
              </div>
              <button type="submit" class="btn-primary" style="width:100px;">Apply</button>
            </form>
            """

        def _code_filters(self, params: dict[str, str]) -> str:
            return f"""
            <form class="filters" method="get" action="{self._url('/codes')}">
              <div style="display:grid; gap:6px;">
                  <label>Search</label>
                  <input name="search" value="{h(params.get('search',''))}">
              </div>
              <div style="display:grid; gap:6px;">
                  <label>Class</label>
                  <input name="class" value="{h(params.get('class',''))}">
              </div>
              <div style="display:grid; gap:6px;">
                  <label>Active</label>
                  <select name="active">{options(['','yes','no'], params.get('active',''))}</select>
              </div>
              <button type="submit" class="btn-primary" style="width:100px;">Apply</button>
            </form>
            """

        def _table_filters(self, table: str, params: dict[str, str]) -> str:
            return f"""
            <form class="filters" method="get" action="{self._url('/table')}">
              <input type="hidden" name="name" value="{h(table)}">
              <div style="display:grid; gap:6px;">
                  <label>Search</label>
                  <input name="search" value="{h(params.get('search',''))}">
              </div>
              <div style="display:grid; gap:6px;">
                  <label>Sort</label>
                  <input name="sort" value="{h(params.get('sort',''))}">
              </div>
              <div style="display:grid; gap:6px;">
                  <label>Order</label>
                  <select name="order">{options(['desc','asc'], params.get('order','desc'))}</select>
              </div>
              <button type="submit" class="btn-primary" style="width:100px;">Apply</button>
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
    table_name: str = "",
    can_manage_users: bool = False,
    existing_users: dict[str, dict[str, str]] | None = None,
) -> str:
    columns = columns or columns_from_rows(rows)
    if not columns:
        return "<p class='muted' style='padding:16px;'>No columns.</p>"
    if not rows:
        return "<p class='muted' style='padding:16px;'>No rows.</p>"
    
    show_auth_actions = table_name == "auth_logs" and can_manage_users
    head = "".join(f"<th>{h(col)}</th>" for col in columns)
    if show_auth_actions:
        head += "<th>Actions</th>"

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
            if col == "severity" and value == "error":
                cell = '<span class="status out">ERROR</span>'
            elif col == "severity" and value == "warning":
                cell = '<span class="status neutral">WARN</span>'
            cells.append(f"<td>{cell}</td>")
        
        if show_auth_actions:
            method = row.get("method")
            identifier = row.get("identifier")
            btn = ""
            if method in ("google", "token") and identifier:
                current_role = ""
                if existing_users:
                    users_dict = existing_users.get(method, {})
                    lookup_id = identifier if method == "token" else identifier.lower()
                    current_role = users_dict.get(lookup_id, "")
                
                if current_role:
                    btn = f'''<button type="button" class="btn-action neutral" onclick="openUserModal('modify', '{h(method)}', '{h(identifier)}', '{h(current_role)}')">Modify</button>'''
                else:
                    btn = f'''<button type="button" class="btn-action add" onclick="openUserModal('add', '{h(method)}', '{h(identifier)}', '')">Add</button>'''
            cells.append(f"<td>{btn}</td>")

        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    return f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table></div>"


CSS = """
:root {
  color-scheme: light;
  --bg: #f9fafb;
  --surface: #ffffff;
  --line: #e5e7eb;
  --text: #111827;
  --muted: #6b7280;
  --accent: #2563eb;
  --accent-hover: #1d4ed8;
  --accent-soft: #eff6ff;
  --danger: #ef4444;
  --danger-soft: #fef2f2;
  --ok: #10b981;
  --ok-soft: #ecfdf5;
  --shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1);
  --radius: 12px;
  --font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-family);
  font-size: 14px;
  -webkit-font-smoothing: antialiased;
}

/* Typography */
h1, h2, h3 { margin: 0; font-weight: 600; color: #111827; }
h1 { font-size: 24px; letter-spacing: -0.025em; }
h2 { font-size: 18px; letter-spacing: -0.025em; }
.muted { color: var(--muted); }
.error { color: var(--danger); font-weight: 500; }

/* Layout */
header {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 16px 32px;
  border-bottom: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.8);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}
header .brand { display: flex; flex-direction: column; }
header strong { font-size: 16px; font-weight: 600; letter-spacing: -0.01em; }
header span { color: var(--muted); font-size: 12px; margin-top: 2px;}

nav { display: flex; gap: 8px; flex-wrap: wrap; }
nav a, .section-head a, button {
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px 14px;
  text-decoration: none;
  font: inherit;
  font-weight: 500;
  font-size: 13px;
  transition: all 0.15s ease;
  cursor: pointer;
}
nav a:hover, .section-head a:hover, button:hover {
  background: #f3f4f6;
}
nav a.active {
  background: var(--text);
  color: #fff;
  border-color: var(--text);
}
.logout-btn {
  margin-left: 8px;
  background: var(--danger-soft) !important;
  color: var(--danger) !important;
  border-color: var(--danger-soft) !important;
}
.logout-btn:hover {
  background: var(--danger) !important;
  color: #fff !important;
  border-color: var(--danger) !important;
}

main {
  max-width: 1200px;
  margin: 32px auto 64px;
  padding: 0 24px;
}
section { margin-top: 32px; }

/* Cards & Metrics */
.metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 16px;
}
.metric {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 20px;
  box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}
.metric:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow);
}
.metric span { display: block; color: var(--muted); font-size: 13px; font-weight: 500; text-transform: uppercase; letter-spacing: 0.05em; }
.metric strong { display: block; margin-top: 12px; font-size: 32px; font-weight: 600; letter-spacing: -0.025em; }

.grid.two {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

/* Forms & Filters */
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  align-items: end;
  padding: 20px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  margin-bottom: 24px;
  box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
}
label { display: grid; gap: 6px; color: #374151; font-size: 13px; font-weight: 500;}
input, select, textarea {
  min-height: 38px;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px 12px;
  background: #fff;
  color: var(--text);
  font: inherit;
  font-size: 13px;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
  outline: none;
}
input:focus, select:focus, textarea:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
textarea {
  width: 100%;
  min-height: 160px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  resize: vertical;
}

.sql-form {
  display: grid;
  gap: 16px;
  padding: 24px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
}
.sql-form button { justify-self: start; background: var(--text); color: white; border-color: var(--text); }
.sql-form button:hover { background: #374151; }

/* Tables */
.table-wrap {
  overflow-x: auto;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
}
table {
  width: 100%;
  border-collapse: collapse;
  white-space: nowrap;
  font-size: 13px;
}
th, td {
  padding: 12px 16px;
  border-bottom: 1px solid var(--line);
  text-align: left;
}
th {
  background: #f9fafb;
  color: #374151;
  font-weight: 600;
  position: sticky;
  top: 0;
  z-index: 10;
}
tbody tr:hover { background: #f9fafb; }
tbody tr:last-child td { border-bottom: none; }
td {
  max-width: 400px;
  overflow: hidden;
  text-overflow: ellipsis;
  color: #4b5563;
}
td a { color: var(--accent); font-weight: 500; text-decoration: none; }
td a:hover { text-decoration: underline; }

/* Status Badges */
.status {
  display: inline-flex;
  align-items: center;
  border-radius: 9999px;
  padding: 3px 10px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.025em;
}
.status.in { background: var(--ok-soft); color: var(--ok); }
.status.out { background: var(--danger-soft); color: var(--danger); }
.status.neutral { background: #f3f4f6; color: #4b5563; }

/* Login Page */
.login-container {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: calc(100vh - 160px);
}
.login-box {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 48px;
  width: 100%;
  max-width: 420px;
  box-shadow: var(--shadow);
}
.login-header { text-align: center; margin-bottom: 32px; }
.login-header h2 { font-size: 24px; margin-bottom: 8px; }
.login-header p { color: var(--muted); margin: 0; }
.input-group { margin-bottom: 24px; }
.input-group input { width: 100%; padding: 12px 14px; font-size: 14px; }
.btn-primary {
  width: 100%;
  padding: 12px;
  background: var(--text);
  color: white;
  border: none;
  font-size: 14px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s ease;
}
.btn-primary:hover { background: #374151; }
.google-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  width: 100%;
  padding: 12px;
  background: #fff;
  color: #3c4043;
  border: 1px solid var(--line);
  border-radius: 8px;
  font: inherit;
  font-size: 14px;
  font-weight: 500;
  text-decoration: none;
  cursor: pointer;
  transition: background 0.15s ease, box-shadow 0.15s ease;
}
.google-btn:hover {
  background: #f8f9fa;
  box-shadow: 0 1px 3px rgba(0,0,0,0.12);
}

/* Toast notifications */
.toast {
  padding: 12px 20px;
  border-radius: var(--radius);
  font-weight: 500;
  font-size: 13px;
  margin-bottom: 16px;
  animation: toast-in 0.3s ease;
}
.toast.success { background: var(--ok-soft); color: #065f46; border: 1px solid #a7f3d0; }
.toast.error { background: var(--danger-soft); color: #991b1b; border: 1px solid #fecaca; }
@keyframes toast-in { from { opacity: 0; transform: translateY(-8px); } to { opacity: 1; transform: translateY(0); } }

/* Action buttons (inline in tables) */
.btn-action {
  display: inline-flex;
  align-items: center;
  padding: 4px 12px;
  border: none;
  border-radius: 6px;
  font: inherit;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
  white-space: nowrap;
}
.btn-action.remove { background: var(--danger-soft); color: var(--danger); }
.btn-action.remove:hover { background: var(--danger); color: #fff; }
.btn-action.add { background: var(--ok-soft); color: #065f46; }
.btn-action.add:hover { background: var(--ok); color: #fff; }
.btn-action.neutral { background: var(--accent-soft); color: var(--accent); }
.btn-action.neutral:hover { background: var(--accent); color: #fff; }

/* Action panel (expandable form) */
.action-panel {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  margin-bottom: 16px;
}
.action-panel summary {
  padding: 12px 20px;
  cursor: pointer;
  font-weight: 500;
  color: var(--accent);
  user-select: none;
}
.action-panel summary:hover { background: var(--accent-soft); border-radius: var(--radius); }
.action-form {
  display: flex;
  gap: 12px;
  align-items: flex-end;
  padding: 16px 20px;
  flex-wrap: wrap;
}

/* Inline select in tables */
.inline-select {
  padding: 4px 8px;
  border: 1px solid var(--line);
  border-radius: 6px;
  font: inherit;
  font-size: 12px;
  background: var(--surface);
}

/* Cards */
.card {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
}

section { margin-bottom: 24px; }
section h2 { margin-bottom: 12px; }

@media (max-width: 860px) {
  header { flex-direction: column; align-items: flex-start; padding: 16px 20px; }
  main { padding: 0 16px; margin-top: 24px; }
  .grid.two { grid-template-columns: 1fr; }
  .filters { display: grid; }
  label, input, select, button { width: 100%; }
  .action-form { flex-direction: column; }
}
"""

if __name__ == "__main__":
    raise SystemExit(main())
