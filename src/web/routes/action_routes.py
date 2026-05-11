import datetime
import sqlite3
import urllib.parse
from fastapi import APIRouter, Request, Depends, Form, HTTPException, status
from fastapi.responses import RedirectResponse

from ...admin_queries import (
    resolve_db_path,
    connect_writable,
    manual_add_item,
    manual_remove_item,
    toggle_code_active
)
from ...secure_codes import SecureCodeError, create_secure_payload, load_code_secret, verify_secure_payload
from ..auth import require_auth, _has_perm, validate_csrf, load_config, save_config

router = APIRouter()

def _redirect_back(request: Request, toast_msg: str, is_error: bool = False) -> RedirectResponse:
    referer = request.headers.get("referer", "/")
    # Parse referer to strip old toast messages and append the new one.
    parsed = urllib.parse.urlparse(referer)
    query = dict(urllib.parse.parse_qsl(parsed.query))
    query.pop("toast_success", None)
    query.pop("toast_error", None)
    if is_error:
        query["toast_error"] = toast_msg
    else:
        query["toast_success"] = toast_msg
    
    new_query = urllib.parse.urlencode(query)
    new_url = urllib.parse.urlunparse(parsed._replace(query=new_query))
    return RedirectResponse(url=new_url, status_code=status.HTTP_302_FOUND)

@router.post("/action/add")
async def action_add(
    request: Request,
    csrf_token: str = Form(...),
    uid: str = Form(...),
    item_class: str = Form(...),
    user: dict = Depends(require_auth)
):
    validate_csrf(request, csrf_token)
    if not _has_perm(user["role"], "manual_add"):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    db_path = resolve_db_path()
    try:
        with connect_writable(db_path) as conn:
            msg = manual_add_item(conn, uid, item_class, user)
        return _redirect_back(request, msg)
    except Exception as e:
        return _redirect_back(request, str(e), is_error=True)

@router.post("/action/remove")
async def action_remove(
    request: Request,
    csrf_token: str = Form(...),
    uid: str = Form(...),
    user: dict = Depends(require_auth)
):
    validate_csrf(request, csrf_token)
    if not _has_perm(user["role"], "manual_remove"):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    db_path = resolve_db_path()
    try:
        with connect_writable(db_path) as conn:
            msg = manual_remove_item(conn, uid, user)
        return _redirect_back(request, msg)
    except Exception as e:
        return _redirect_back(request, str(e), is_error=True)

@router.post("/action/add_code")
async def action_add_code(
    request: Request,
    csrf_token: str = Form(...),
    uid: str = Form(...),
    item_class: str = Form(...),
    user: dict = Depends(require_auth)
):
    validate_csrf(request, csrf_token)
    if not _has_perm(user["role"], "manage_codes"):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    uid = uid.strip().upper()
    item_class = item_class.strip()
    if not uid or not item_class:
        return _redirect_back(request, "UID and Class are required.", is_error=True)

    try:
        secret = load_code_secret(load_config())
        payload = create_secure_payload(item_class, secret, public_uid=uid)
        secure_code = verify_secure_payload(payload, secret)
    except SecureCodeError as e:
        return _redirect_back(request, f"Invalid code: {e}", is_error=True)

    db_path = resolve_db_path()
    try:
        with connect_writable(db_path) as conn:
            from ...admin_queries import log_admin_action
            conn.execute(
                """
                INSERT INTO registered_codes (public_uid, payload, item_class, active, created_at)
                VALUES (?, ?, ?, 1, ?)
                """,
                (
                    secure_code.public_uid,
                    secure_code.payload,
                    secure_code.item_class,
                    datetime.datetime.now().isoformat(),
                )
            )
            log_admin_action(conn, "ADD_CODE", secure_code.public_uid, user, f"Class: {secure_code.item_class}")
            conn.commit()
        return _redirect_back(request, f"Code '{secure_code.public_uid}' registered.")
    except sqlite3.IntegrityError:
        return _redirect_back(request, f"Code '{secure_code.public_uid}' already exists.", is_error=True)
    except Exception as e:
        return _redirect_back(request, f"Error adding code: {e}", is_error=True)

@router.post("/action/add_user")
@router.post("/action/change_role")
async def action_manage_user(
    request: Request,
    csrf_token: str = Form(...),
    auth_type: str = Form(None), # Old name compatibility
    user_type: str = Form(None), 
    email: str = Form(None),     # Old name compatibility
    token: str = Form(None),     # Old name compatibility
    user_id: str = Form(None),
    role: str = Form(...),
    current_user: dict = Depends(require_auth)
):
    validate_csrf(request, csrf_token)
    if not _has_perm(current_user["role"], "manage_users"):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    # Coalesce values
    u_type = user_type or auth_type
    u_id = user_id or (email if u_type == "google" else token)
    
    if not u_type or not u_id:
        return _redirect_back(request, "Type and Identifier are required.", is_error=True)
        
    u_id = u_id.strip()
    config = load_config()
    web_config = config.get("web_admin", {})
    if "users" not in web_config:
        web_config["users"] = []
    if "google_oauth" not in web_config:
        web_config["google_oauth"] = {}
    if "users" not in web_config["google_oauth"]:
        web_config["google_oauth"]["users"] = []
        
    if u_type == "token":
        users = web_config["users"]
        found = False
        for u in users:
            if str(u.get("token")) == u_id:
                u["role"] = role
                found = True
                break
        if not found:
            users.append({"token": u_id, "role": role})
            
    elif u_type == "google":
        users = web_config["google_oauth"]["users"]
        found = False
        for u in users:
            if str(u.get("email", "")).lower() == u_id.lower():
                u["role"] = role
                found = True
                break
        if not found:
            users.append({"email": u_id.lower(), "role": role})
    else:
        return _redirect_back(request, f"Unknown user type: {u_type}", is_error=True)
        
    save_config(config)
    
    # Log it
    db_path = resolve_db_path()
    try:
        with connect_writable(db_path) as conn:
            from ...admin_queries import log_admin_action
            log_admin_action(conn, "MANAGE_USER", f"{u_type}:{u_id}", current_user, f"Set role: {role}")
            conn.commit()
    except Exception:
        pass
        
    return _redirect_back(request, f"Role for {u_id} updated to {role}.")

@router.post("/action/remove_user")
async def action_remove_user(
    request: Request,
    csrf_token: str = Form(...),
    user_type: str = Form(...),
    user_id: str = Form(...),
    current_user: dict = Depends(require_auth)
):
    validate_csrf(request, csrf_token)
    if not _has_perm(current_user["role"], "manage_users"):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    u_id = user_id.strip()
    config = load_config()
    web_config = config.get("web_admin", {})
    
    removed = False
    if user_type == "token" and "users" in web_config:
        users = web_config["users"]
        new_users = [u for u in users if str(u.get("token")) != u_id]
        if len(new_users) < len(users):
            web_config["users"] = new_users
            removed = True
            
    elif user_type == "google" and "google_oauth" in web_config and "users" in web_config["google_oauth"]:
        users = web_config["google_oauth"]["users"]
        new_users = [u for u in users if str(u.get("email", "")).lower() != u_id.lower()]
        if len(new_users) < len(users):
            web_config["google_oauth"]["users"] = new_users
            removed = True
            
    if removed:
        save_config(config)
        
        # Log it
        db_path = resolve_db_path()
        try:
            with connect_writable(db_path) as conn:
                from ...admin_queries import log_admin_action
                log_admin_action(conn, "REMOVE_USER", f"{user_type}:{u_id}", current_user, "Removed access")
                conn.commit()
        except Exception:
            pass
            
        return _redirect_back(request, f"User {u_id} removed.")
    else:
        return _redirect_back(request, f"User {u_id} not found.", is_error=True)


@router.post("/action/toggle_code")
async def action_toggle_code(
    request: Request,
    csrf_token: str = Form(...),
    public_uid: str = Form(...),
    user: dict = Depends(require_auth)
):
    validate_csrf(request, csrf_token)
    if not _has_perm(user["role"], "manage_codes"):
        raise HTTPException(status_code=403, detail="Forbidden")

    db_path = resolve_db_path()
    try:
        with connect_writable(db_path) as conn:
            msg = toggle_code_active(conn, public_uid, user)
        return _redirect_back(request, msg)
    except Exception as e:
        return _redirect_back(request, str(e), is_error=True)
