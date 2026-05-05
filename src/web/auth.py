import secrets
import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional
from fastapi import Request, HTTPException, status, Depends
import urllib.parse

# Load config
CONFIG_PATH = Path("config.yaml")

def load_config() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}

def save_config(config: Dict[str, Any]) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, sort_keys=False)

def get_current_user(request: Request) -> Optional[Dict[str, str]]:
    """Returns {'method': 'token'|'google', 'identifier': '...', 'role': '...'} or None"""
    session = request.session
    if not session.get("authenticated"):
        return None
    method = session.get("method")
    identifier = session.get("identifier")
    
    config = load_config()
    web_config = config.get("web_admin", {})
    
    role = None
    if method == "token":
        users = web_config.get("users", [])
        for u in users:
            if str(u.get("token")) == str(identifier):
                role = u.get("role")
                break
    elif method == "google":
        oauth_users = web_config.get("google_oauth", {}).get("users", [])
        for u in oauth_users:
            if str(u.get("email", "")).lower() == str(identifier).lower():
                role = u.get("role")
                break
    
    if not role:
        return None
    
    return {"method": method, "identifier": identifier, "role": role}

def require_auth(request: Request) -> Dict[str, str]:
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": "/login"}
        )
    return user

def _has_perm(role: str, perm: str, config: Dict[str, Any] = None) -> bool:
    if config is None:
        config = load_config()
    roles_def = config.get("web_admin", {}).get("roles", {})
    perms = roles_def.get(role, {}).get("permissions", [])
    return "*" in perms or perm in perms

def require_permission(perm: str):
    def dependency(request: Request, user: Dict[str, str] = Depends(require_auth)):
        config = load_config()
        if not _has_perm(user["role"], perm, config):
            raise HTTPException(status_code=403, detail="Forbidden")
        return user
    return dependency

def generate_csrf_token(request: Request) -> str:
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_hex(16)
    return request.session["csrf_token"]

def validate_csrf(request: Request, token: str) -> None:
    expected = request.session.get("csrf_token")
    if not expected or not secrets.compare_digest(str(expected), str(token)):
        raise HTTPException(status_code=400, detail="Invalid CSRF token")
