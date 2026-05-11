import asyncio
import datetime
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.database import InventoryDatabase
from src.admin_queries import (
    connect_writable,
    get_inventory_items,
    get_logs,
    get_registered_codes,
    manual_add_item,
    manual_remove_item,
    repair_public_uid_inventory_duplicates,
)
from src.secure_codes import verify_secure_payload
from src.web.auth import _has_perm
from src.web.routes import action_routes, auth_routes
from src.web.utils import table_html


SECRET = "0123456789abcdef0123456789abcdef"


class FakeRequest:
    def __init__(self):
        self.headers = {"referer": "/codes"}
        self.session = {"csrf_token": "csrf"}


class WebAdminTests(unittest.TestCase):
    def make_db_path(self):
        temp_dir = tempfile.TemporaryDirectory()
        db_path = os.path.join(temp_dir.name, "inventory.db")
        db = InventoryDatabase(db_path)
        db.close()
        self.addCleanup(temp_dir.cleanup)
        return db_path

    def add_registered_code(self, conn, public_uid="AB12CD34", item_class="multicet", payload=None):
        payload = payload or f"payload-{public_uid}"
        conn.execute(
            """
            INSERT INTO registered_codes (public_uid, payload, item_class, active, created_at)
            VALUES (?, ?, ?, 1, ?)
            """,
            (public_uid, payload, item_class, datetime.datetime.now().isoformat()),
        )
        return payload

    def test_manual_add_public_uid_uses_canonical_inventory_uid(self):
        db_path = self.make_db_path()
        user = {"method": "token", "identifier": "tester", "role": "admin"}

        with connect_writable(db_path) as conn, mock.patch("src.admin_queries.trigger_webhook"):
            self.add_registered_code(conn, public_uid="AB12CD34", item_class="multicet")
            conn.execute(
                "INSERT INTO items (uid, item_class, in_stock) VALUES (?, ?, 0)",
                ("multicet_AB12CD34", "multicet"),
            )

            message = manual_add_item(conn, "AB12CD34", "multicet", user)

            self.assertEqual(message, "Item 'multicet_AB12CD34' added to inventory.")
            public_row = conn.execute("SELECT uid FROM items WHERE uid = ?", ("AB12CD34",)).fetchone()
            canonical_row = conn.execute(
                "SELECT item_class, in_stock FROM items WHERE uid = ?",
                ("multicet_AB12CD34",),
            ).fetchone()
            log_row = conn.execute("SELECT uid, action FROM logs ORDER BY id DESC LIMIT 1").fetchone()

        self.assertIsNone(public_row)
        self.assertEqual(dict(canonical_row), {"item_class": "multicet", "in_stock": 1})
        self.assertEqual(dict(log_row), {"uid": "multicet_AB12CD34", "action": "ADDED"})

    def test_manual_add_registered_payload_uses_canonical_inventory_uid(self):
        db_path = self.make_db_path()
        user = {"method": "token", "identifier": "tester", "role": "admin"}

        with connect_writable(db_path) as conn, mock.patch("src.admin_queries.trigger_webhook"):
            payload = self.add_registered_code(conn, public_uid="CD34EF56", item_class="led_box")

            manual_add_item(conn, payload, "", user)

            row = conn.execute(
                "SELECT uid, item_class, in_stock FROM items WHERE uid = ?",
                ("led_box_CD34EF56",),
            ).fetchone()

        self.assertEqual(dict(row), {"uid": "led_box_CD34EF56", "item_class": "led_box", "in_stock": 1})

    def test_manual_add_public_uid_rejects_already_in_stock_canonical_item(self):
        db_path = self.make_db_path()
        user = {"method": "token", "identifier": "tester", "role": "admin"}

        with connect_writable(db_path) as conn, mock.patch("src.admin_queries.trigger_webhook"):
            self.add_registered_code(conn, public_uid="AB12CD34", item_class="multicet")
            conn.execute(
                "INSERT INTO items (uid, item_class, in_stock) VALUES (?, ?, 1)",
                ("multicet_AB12CD34", "multicet"),
            )

            with self.assertRaisesRegex(ValueError, "already in stock"):
                manual_add_item(conn, "AB12CD34", "multicet", user)

            public_row = conn.execute("SELECT uid FROM items WHERE uid = ?", ("AB12CD34",)).fetchone()

        self.assertIsNone(public_row)

    def test_manual_remove_public_uid_uses_canonical_inventory_uid(self):
        db_path = self.make_db_path()
        user = {"method": "token", "identifier": "tester", "role": "manager"}

        with connect_writable(db_path) as conn, mock.patch("src.admin_queries.trigger_webhook"):
            self.add_registered_code(conn, public_uid="AB12CD34", item_class="multicet")
            conn.execute(
                "INSERT INTO items (uid, item_class, in_stock) VALUES (?, ?, 1)",
                ("multicet_AB12CD34", "multicet"),
            )

            message = manual_remove_item(conn, "AB12CD34", user)

            self.assertEqual(message, "Item 'multicet_AB12CD34' removed from inventory.")
            public_row = conn.execute("SELECT uid FROM items WHERE uid = ?", ("AB12CD34",)).fetchone()
            canonical_row = conn.execute(
                "SELECT item_class, in_stock FROM items WHERE uid = ?",
                ("multicet_AB12CD34",),
            ).fetchone()
            log_row = conn.execute("SELECT uid, action FROM logs ORDER BY id DESC LIMIT 1").fetchone()

        self.assertIsNone(public_row)
        self.assertEqual(dict(canonical_row), {"item_class": "multicet", "in_stock": 0})
        self.assertEqual(dict(log_row), {"uid": "multicet_AB12CD34", "action": "REMOVED"})

    def test_manual_remove_permission_is_admin_or_manager_only(self):
        config = {
            "web_admin": {
                "roles": {
                    "admin": {"permissions": ["*"]},
                    "manager": {"permissions": ["manual_remove"]},
                    "viewer": {"permissions": ["view_inventory"]},
                }
            }
        }

        self.assertTrue(_has_perm("admin", "manual_remove", config))
        self.assertTrue(_has_perm("manager", "manual_remove", config))
        self.assertFalse(_has_perm("viewer", "manual_remove", config))

    def test_inventory_template_has_permission_gated_manual_remove_form(self):
        template_path = os.path.join(REPO_ROOT, "src", "web", "templates", "items.html")
        with open(template_path, encoding="utf-8") as f:
            template = f.read()

        self.assertIn("{% if can_remove %}", template)
        self.assertIn('action="/action/remove"', template)
        self.assertIn("Manual Remove Item", template)

    def test_repair_public_uid_inventory_duplicates_merges_rows(self):
        db_path = self.make_db_path()

        with connect_writable(db_path) as conn:
            self.add_registered_code(conn, public_uid="AB12CD34", item_class="multicet")
            conn.execute(
                "INSERT INTO items (uid, item_class, in_stock) VALUES (?, ?, ?)",
                ("multicet_AB12CD34", "multicet", 0),
            )
            conn.execute(
                "INSERT INTO items (uid, item_class, in_stock) VALUES (?, ?, ?)",
                ("AB12CD34", "multicet", 1),
            )
            conn.execute(
                "INSERT INTO logs (uid, action, timestamp) VALUES (?, ?, ?)",
                ("multicet_AB12CD34", "ADDED", "2026-05-08T10:00:00"),
            )
            conn.execute(
                "INSERT INTO logs (uid, action, timestamp) VALUES (?, ?, ?)",
                ("multicet_AB12CD34", "REMOVED", "2026-05-08T10:01:00"),
            )
            conn.execute(
                "INSERT INTO logs (uid, action, timestamp) VALUES (?, ?, ?)",
                ("AB12CD34", "ADDED", "2026-05-08T10:02:00"),
            )
            conn.execute(
                """
                CREATE TABLE admin_audit_logs (
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
                VALUES
                    ('2026-05-08T10:02:00', 'token', 'tester', 'admin', 'MANUAL_ADD', 'AB12CD34', 'Class: multicet'),
                    ('2026-05-08T09:00:00', 'token', 'tester', 'admin', 'ADD_CODE', 'AB12CD34', 'Class: multicet')
                """
            )

            stats = repair_public_uid_inventory_duplicates(conn)
            public_row = conn.execute("SELECT uid FROM items WHERE uid = ?", ("AB12CD34",)).fetchone()
            canonical_row = conn.execute(
                "SELECT uid, item_class, in_stock FROM items WHERE uid = ?",
                ("multicet_AB12CD34",),
            ).fetchone()
            public_logs = conn.execute("SELECT COUNT(*) AS count FROM logs WHERE uid = ?", ("AB12CD34",)).fetchone()
            manual_audit = conn.execute(
                "SELECT entity_id FROM admin_audit_logs WHERE action_type = 'MANUAL_ADD'"
            ).fetchone()
            code_audit = conn.execute(
                "SELECT entity_id FROM admin_audit_logs WHERE action_type = 'ADD_CODE'"
            ).fetchone()

        self.assertEqual(stats["duplicate_rows"], 1)
        self.assertEqual(stats["log_updates"], 1)
        self.assertEqual(stats["audit_updates"], 1)
        self.assertEqual(stats["deleted_public_uid_rows"], 1)
        self.assertIsNone(public_row)
        self.assertEqual(dict(canonical_row), {"uid": "multicet_AB12CD34", "item_class": "multicet", "in_stock": 1})
        self.assertEqual(public_logs["count"], 0)
        self.assertEqual(manual_audit["entity_id"], "multicet_AB12CD34")
        self.assertEqual(code_audit["entity_id"], "AB12CD34")

    def test_class_filters_accept_canonical_and_legacy_query_names(self):
        db_path = self.make_db_path()

        with connect_writable(db_path) as conn:
            self.add_registered_code(conn, public_uid="AB12CD34", item_class="multicet")
            self.add_registered_code(conn, public_uid="CD34EF56", item_class="led_box")
            conn.execute(
                "INSERT INTO items (uid, item_class, in_stock) VALUES (?, ?, 1)",
                ("multicet_AB12CD34", "multicet"),
            )
            conn.execute(
                "INSERT INTO items (uid, item_class, in_stock) VALUES (?, ?, 1)",
                ("led_box_CD34EF56", "led_box"),
            )
            conn.execute(
                "INSERT INTO logs (uid, action, timestamp) VALUES (?, ?, ?)",
                ("multicet_AB12CD34", "ADDED", "2026-05-08T10:00:00"),
            )
            conn.execute(
                "INSERT INTO logs (uid, action, timestamp) VALUES (?, ?, ?)",
                ("led_box_CD34EF56", "ADDED", "2026-05-08T10:01:00"),
            )

            inventory = get_inventory_items(conn, {"item_class": "multicet"})
            legacy_inventory = get_inventory_items(conn, {"class": "multicet"})
            logs = get_logs(conn, {"item_class": "multicet"})
            legacy_codes = get_registered_codes(conn, {"class": "multicet"})

        self.assertEqual(inventory["total"], 1)
        self.assertEqual(legacy_inventory["total"], 1)
        self.assertEqual(logs["total"], 1)
        self.assertEqual(legacy_codes["total"], 1)
        self.assertEqual(inventory["rows"][0]["uid"], "multicet_AB12CD34")
        self.assertEqual(logs["rows"][0]["uid"], "multicet_AB12CD34")

    def test_add_code_route_registers_signed_payload(self):
        db_path = self.make_db_path()
        user = {"method": "token", "identifier": "tester", "role": "admin"}
        config = {
            "security": {"secret_key": SECRET},
        }

        with mock.patch.object(action_routes, "resolve_db_path", return_value=db_path), \
             mock.patch.object(action_routes, "load_config", return_value=config), \
             mock.patch.object(action_routes, "_has_perm", return_value=True):
            response = asyncio.run(
                action_routes.action_add_code(
                    FakeRequest(),
                    csrf_token="csrf",
                    uid="AB12CD34",
                    item_class="multicet",
                    user=user,
                )
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("toast_success=Code+%27AB12CD34%27+registered.", response.headers["location"])

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT public_uid, payload, item_class, active FROM registered_codes WHERE public_uid = ?",
                ("AB12CD34",),
            ).fetchone()

        self.assertIsNotNone(row)
        public_uid, payload, item_class, active = row
        self.assertEqual(public_uid, "AB12CD34")
        self.assertEqual(item_class, "multicet")
        self.assertEqual(active, 1)

        secure_code = verify_secure_payload(payload, SECRET)
        self.assertEqual(secure_code.public_uid, "AB12CD34")
        self.assertEqual(secure_code.item_class, "multicet")

    def test_add_code_route_reports_duplicate_uid(self):
        db_path = self.make_db_path()
        user = {"method": "token", "identifier": "tester", "role": "admin"}
        config = {
            "security": {"secret_key": SECRET},
        }

        with mock.patch.object(action_routes, "resolve_db_path", return_value=db_path), \
             mock.patch.object(action_routes, "load_config", return_value=config), \
             mock.patch.object(action_routes, "_has_perm", return_value=True):
            asyncio.run(
                action_routes.action_add_code(
                    FakeRequest(),
                    csrf_token="csrf",
                    uid="AB12CD34",
                    item_class="multicet",
                    user=user,
                )
            )
            response = asyncio.run(
                action_routes.action_add_code(
                    FakeRequest(),
                    csrf_token="csrf",
                    uid="AB12CD34",
                    item_class="multicet",
                    user=user,
                )
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("toast_error=Code+%27AB12CD34%27+already+exists.", response.headers["location"])

    def test_code_toggle_table_renders_action_buttons(self):
        html = table_html(
            [{"public_uid": "AB12CD34", "active": 1}],
            ["public_uid", "active"],
            code_toggle=True,
        )

        self.assertIn("Deactivate", html)
        self.assertIn("toggleCode", html)
        self.assertIn("AB12CD34", html)

    def test_table_html_includes_mobile_card_labels(self):
        html = table_html(
            [{"uid": "multicet_AB12CD34", "item_class": "multicet", "in_stock": 1}],
            ["uid", "item_class", "in_stock"],
            trace_links=True,
        )

        self.assertIn("responsive-data-table", html)
        self.assertIn('data-label="uid"', html)
        self.assertIn('data-label="item_class"', html)
        self.assertIn('data-label="in_stock"', html)

    def test_base_template_has_mobile_responsive_rules(self):
        template_path = os.path.join(REPO_ROOT, "src", "web", "templates", "base.html")
        with open(template_path, encoding="utf-8") as f:
            template = f.read()

        self.assertIn("@media (max-width: 640px)", template)
        self.assertIn(".responsive-data-table td::before", template)
        self.assertIn("grid-template-columns: minmax(104px, 38%) minmax(0, 1fr)", template)

    def test_auth_logging_migrates_existing_auth_log_schema(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db_path = os.path.join(temp_dir.name, "inventory.db")

        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE auth_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME,
                    method TEXT,
                    identifier TEXT,
                    success INTEGER,
                    role TEXT,
                    ip_address TEXT
                )
                """
            )

        with mock.patch("src.admin_queries.resolve_db_path", return_value=db_path):
            auth_routes._log_auth_attempt(
                "token",
                "token:***1234",
                True,
                ip="127.0.0.1",
                role="admin",
            )

        with sqlite3.connect(db_path) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(auth_logs)").fetchall()}
            row = conn.execute(
                "SELECT method, identifier, success, role, ip_address, ip, detail FROM auth_logs"
            ).fetchone()

        self.assertIn("ip", columns)
        self.assertIn("detail", columns)
        self.assertEqual(row, ("token", "token:***1234", 1, "admin", "127.0.0.1", "127.0.0.1", ""))


if __name__ == "__main__":
    unittest.main()
