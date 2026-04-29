import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.database import InventoryDatabase
from src.secure_codes import (
    SecureCodeError,
    create_secure_payload,
    validate_registered_code,
    verify_secure_payload,
)


SECRET = b"0123456789abcdef0123456789abcdef"


class SecureCodeTests(unittest.TestCase):
    def make_db(self):
        temp_dir = tempfile.TemporaryDirectory()
        db = InventoryDatabase(os.path.join(temp_dir.name, "inventory.db"))
        self.addCleanup(db.close)
        self.addCleanup(temp_dir.cleanup)
        return db

    def test_generated_code_verifies_and_is_registered(self):
        db = self.make_db()
        payload = create_secure_payload("multicet", SECRET)
        secure_code = verify_secure_payload(payload, SECRET)

        db.register_code(secure_code.public_uid, secure_code.payload, secure_code.item_class)

        registered = validate_registered_code(payload, SECRET, db)
        self.assertEqual(registered.inventory_uid, secure_code.inventory_uid)
        self.assertEqual(registered.item_class, "multicet")

    def test_edited_payloads_are_rejected(self):
        payload = create_secure_payload(
            "multicet",
            SECRET,
            public_uid="AB12CD34",
            random_token="ABCDEFGHIJKLMNOP",
        )
        version, item_class, public_uid, token, signature = payload.split(":")
        edited_payloads = [
            ":".join([version, "kabel", public_uid, token, signature]),
            ":".join([version, item_class, "ZZ99YY88", token, signature]),
            ":".join([version, item_class, public_uid, "QRSTUVWXYZABCDEF", signature]),
            ":".join([version, item_class, public_uid, token, "0" * 64]),
        ]

        for edited_payload in edited_payloads:
            with self.subTest(edited_payload=edited_payload):
                with self.assertRaises(SecureCodeError):
                    verify_secure_payload(edited_payload, SECRET)

    def test_old_sequential_code_is_rejected(self):
        with self.assertRaises(SecureCodeError):
            verify_secure_payload("multicet_1001", SECRET)

    def test_signed_but_unregistered_code_is_rejected(self):
        db = self.make_db()
        payload = create_secure_payload("multicet", SECRET)

        with self.assertRaises(SecureCodeError):
            validate_registered_code(payload, SECRET, db)

    def test_duplicate_registered_code_fails(self):
        db = self.make_db()
        payload = create_secure_payload(
            "multicet",
            SECRET,
            public_uid="AB12CD34",
            random_token="ABCDEFGHIJKLMNOP",
        )
        secure_code = verify_secure_payload(payload, SECRET)
        db.register_code(secure_code.public_uid, secure_code.payload, secure_code.item_class)

        with self.assertRaises(sqlite3.IntegrityError):
            db.register_code(secure_code.public_uid, secure_code.payload, secure_code.item_class)

        second_payload = create_secure_payload(
            "multicet",
            SECRET,
            public_uid=secure_code.public_uid,
            random_token="QRSTUVWXYZABCDEF",
        )
        with self.assertRaises(sqlite3.IntegrityError):
            db.register_code(secure_code.public_uid, second_payload, secure_code.item_class)

    def test_registered_code_cannot_be_added_twice_while_in_stock(self):
        db = self.make_db()
        payload = create_secure_payload("multicet", SECRET)
        secure_code = verify_secure_payload(payload, SECRET)
        db.register_code(secure_code.public_uid, secure_code.payload, secure_code.item_class)

        with redirect_stdout(StringIO()):
            db.log_action(secure_code.inventory_uid, secure_code.item_class, "ADDED")

        self.assertIs(db.check_item_status(secure_code.inventory_uid), True)

    def test_yolo_code_class_mismatch_is_detectable(self):
        payload = create_secure_payload("multicet", SECRET)
        secure_code = verify_secure_payload(payload, SECRET)

        self.assertNotEqual(secure_code.item_class, "kabel")


if __name__ == "__main__":
    unittest.main()
