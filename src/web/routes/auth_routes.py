from fastapi import APIRouter, Request, Form, Response, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import load_config, get_current_user

router = APIRouter()
templates = Jinja2Templates(directory="src/web/templates")


def _log_auth_attempt(method: str, identifier: str, success: bool, ip: str = "", detail: str = "", role: str = ""):
    """Log authentication attempts to auth_logs table."""
    try:
        from ...admin_queries import resolve_db_path, connect_writable
        import datetime
        db_path = resolve_db_path()
        with connect_writable(db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS auth_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    method TEXT NOT NULL,
                    identifier TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    role TEXT,
                    ip_address TEXT,
                    ip TEXT,
                    detail TEXT
                )
            """)
            existing_cols = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(auth_logs)").fetchall()
            }
            for column, ddl in {
                "role": "ALTER TABLE auth_logs ADD COLUMN role TEXT",
                "ip_address": "ALTER TABLE auth_logs ADD COLUMN ip_address TEXT",
                "ip": "ALTER TABLE auth_logs ADD COLUMN ip TEXT",
                "detail": "ALTER TABLE auth_logs ADD COLUMN detail TEXT",
            }.items():
                if column not in existing_cols:
                    conn.execute(ddl)
                    existing_cols.add(column)

            conn.execute(
                """
                INSERT INTO auth_logs
                    (timestamp, method, identifier, success, role, ip_address, ip, detail)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.datetime.now().isoformat(),
                    method,
                    identifier,
                    1 if success else 0,
                    role or "",
                    ip,
                    ip,
                    detail,
                )
            )
            conn.commit()
    except Exception as e:
        print(f"Failed to log auth attempt: {e}")


@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request, error: str = ""):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
        
    config = load_config()
    oauth_enabled = bool(config.get("web_admin", {}).get("google_oauth", {}).get("client_id"))
    is_local = request.client.host in ("127.0.0.1", "::1", "localhost") or request.headers.get("x-forwarded-for") is None
    
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "error": error,
            "oauth_enabled": oauth_enabled,
            "is_local": is_local,
            "csrf_token": ""
        }
    )

@router.post("/login")
async def login_post(request: Request, token: str = Form(...)):
    config = load_config()
    users = config.get("web_admin", {}).get("users", [])
    ip = request.client.host if request.client else "unknown"
    
    for u in users:
        if str(u.get("token")) == str(token):
            request.session["authenticated"] = True
            request.session["method"] = "token"
            request.session["identifier"] = token
            _log_auth_attempt(
                "token",
                f"token:***{token[-4:]}" if len(token) > 4 else "token:***",
                True,
                ip,
                role=u.get("role", ""),
            )
            return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    _log_auth_attempt("token", "invalid_token", False, ip, "Invalid token provided")
    return RedirectResponse(url="/login?error=Invalid+token", status_code=status.HTTP_302_FOUND)

@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

@router.get("/auth/google")
async def auth_google(request: Request):
    config = load_config()
    oauth = config.get("web_admin", {}).get("google_oauth", {})
    client_id = oauth.get("client_id")
    if not client_id or client_id == "PLACEHOLDER_CLIENT_ID":
        raise HTTPException(status_code=400, detail="Google OAuth not configured")
        
    redirect_uri = f"{request.url.scheme}://{request.url.netloc}/auth/google/callback"
    url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        "response_type=code&"
        f"client_id={client_id}&"
        f"redirect_uri={redirect_uri}&"
        "scope=openid%20email%20profile&"
        "access_type=online"
    )
    return RedirectResponse(url)

@router.get("/auth/google/callback")
async def auth_google_callback(request: Request, code: str = None, error: str = None):
    ip = request.client.host if request.client else "unknown"

    if error:
        _log_auth_attempt("google", "unknown", False, ip, f"OAuth error: {error}")
        return RedirectResponse(url=f"/login?error=Google+Auth+Failed:+{error}")
    if not code:
        _log_auth_attempt("google", "unknown", False, ip, "No code provided")
        return RedirectResponse(url="/login?error=No+code+provided")
        
    config = load_config()
    oauth = config.get("web_admin", {}).get("google_oauth", {})
    client_id = oauth.get("client_id")
    client_secret = oauth.get("client_secret")
    redirect_uri = f"{request.url.scheme}://{request.url.netloc}/auth/google/callback"
    
    import requests
    
    # Exchange code for token
    token_resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code"
        },
        timeout=10.0
    )
    if token_resp.status_code != 200:
        err_detail = token_resp.json().get("error_description", token_resp.text)
        print(f"Token exchange failed: {err_detail}")
        import urllib.parse
        safe_err = urllib.parse.quote_plus(str(err_detail))
        _log_auth_attempt("google", "unknown", False, ip, f"Token exchange failed: {err_detail}")
        return RedirectResponse(url=f"/login?error=Failed+to+exchange+token:+{safe_err}")
        
    access_token = token_resp.json().get("access_token")
    
    # Get user info
    user_resp = requests.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10.0
    )
    if user_resp.status_code != 200:
        _log_auth_attempt("google", "unknown", False, ip, "Failed to get user info")
        return RedirectResponse(url="/login?error=Failed+to+get+user+info")
        
    email = user_resp.json().get("email")
    if not email:
        _log_auth_attempt("google", "unknown", False, ip, "No email in OAuth response")
        return RedirectResponse(url="/login?error=No+email+found")
        
    # Check against users
    oauth_users = oauth.get("users", [])
    authorized = False
    authorized_role = ""
    for u in oauth_users:
        if str(u.get("email", "")).lower() == email.lower():
            authorized = True
            authorized_role = u.get("role", "")
            break
            
    if authorized:
        request.session["authenticated"] = True
        request.session["method"] = "google"
        request.session["identifier"] = email
        _log_auth_attempt("google", email, True, ip, role=authorized_role)
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    else:
        _log_auth_attempt("google", email, False, ip, "Email not authorized")
        return RedirectResponse(url="/login?error=Email+not+authorized")
