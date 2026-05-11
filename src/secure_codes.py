import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass


CODE_VERSION = "SC1"
DEFAULT_SECRET_ENV = "SMART_CHECKOUT_CODE_SECRET"
MIN_SECRET_BYTES = 32

_CLASS_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PUBLIC_UID_RE = re.compile(r"^[A-Z0-9]{8,32}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_SIGNATURE_RE = re.compile(r"^[0-9a-f]{64}$")


class SecureCodeError(ValueError):
    """Raised when a scanned Data Matrix payload is not authentic or usable."""


@dataclass(frozen=True)
class SecureCode:
    item_class: str
    public_uid: str
    random_token: str
    signature: str
    payload: str

    @property
    def inventory_uid(self):
        return f"{self.item_class}_{self.public_uid}"


def load_code_secret(config):
    security_config = config.get("security", {}) if config else {}
    env_name = security_config.get("secret_env", DEFAULT_SECRET_ENV)

    # Ensure env_name is a string for os.environ.get
    secret = None
    if isinstance(env_name, str):
        secret = os.environ.get(env_name)

    secret = secret or security_config.get("secret_key")

    if not secret:
        raise SecureCodeError(
            f"Missing Data Matrix signing secret. Set {env_name} environment variable or security.secret_key in config.yaml."
        )

    # Ensure secret is a string (YAML might parse numeric secrets as int)
    secret = str(secret)
    secret_bytes = secret.encode("utf-8")

    if len(secret_bytes) < MIN_SECRET_BYTES:
        raise SecureCodeError(
            f"Data Matrix signing secret must be at least {MIN_SECRET_BYTES} bytes."
        )
    return secret_bytes


def generate_public_uid():
    return secrets.token_hex(4).upper()


def generate_random_token():
    return secrets.token_urlsafe(18)


def create_secure_payload(item_class, secret, public_uid=None, random_token=None):
    secret = _secret_bytes(secret)
    public_uid = public_uid or generate_public_uid()
    random_token = random_token or generate_random_token()
    _validate_unsigned_fields(item_class, public_uid, random_token)

    body = _signing_body(item_class, public_uid, random_token)
    signature = hmac.new(secret, body.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{body}:{signature}"


def verify_secure_payload(payload, secret):
    secret = _secret_bytes(secret)
    parts = payload.split(":")
    if len(parts) != 5:
        raise SecureCodeError("Malformed or unsigned Data Matrix code.")

    version, item_class, public_uid, random_token, signature = parts
    if version != CODE_VERSION:
        raise SecureCodeError("Unsupported Data Matrix code version.")

    _validate_unsigned_fields(item_class, public_uid, random_token)
    if not _SIGNATURE_RE.fullmatch(signature):
        raise SecureCodeError("Invalid Data Matrix signature format.")

    body = _signing_body(item_class, public_uid, random_token)
    expected = hmac.new(secret, body.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise SecureCodeError("Invalid Data Matrix signature.")

    return SecureCode(
        item_class=item_class,
        public_uid=public_uid,
        random_token=random_token,
        signature=signature,
        payload=payload,
    )


def validate_registered_code(payload, secret, inventory_db):
    secure_code = verify_secure_payload(payload, secret)
    registered = inventory_db.get_registered_code(payload)

    if registered is None:
        raise SecureCodeError("Data Matrix code is signed but not registered.")
    if not registered["active"]:
        raise SecureCodeError("Data Matrix code is disabled.")
    if registered["item_class"] != secure_code.item_class:
        raise SecureCodeError("Registered class does not match signed payload.")

    return secure_code


def _signing_body(item_class, public_uid, random_token):
    return f"{CODE_VERSION}:{item_class}:{public_uid}:{random_token}"


def _secret_bytes(secret):
    if isinstance(secret, str):
        return secret.encode("utf-8")
    return secret


def _validate_unsigned_fields(item_class, public_uid, random_token):
    if not _CLASS_RE.fullmatch(item_class):
        raise SecureCodeError("Invalid item class in Data Matrix payload.")
    if not _PUBLIC_UID_RE.fullmatch(public_uid):
        raise SecureCodeError("Invalid public UID in Data Matrix payload.")
    if not _TOKEN_RE.fullmatch(random_token):
        raise SecureCodeError("Invalid random token in Data Matrix payload.")
