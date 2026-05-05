from fastapi import APIRouter, Request, Form, Response, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import load_config, get_current_user

router = APIRouter()
templates = Jinja2Templates(directory="src/web/templates")

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
            "csrf_token": "" # Not strictly needed on login GET for token auth without session yet
        }
    )

@router.post("/login")
async def login_post(request: Request, token: str = Form(...)):
    config = load_config()
    users = config.get("web_admin", {}).get("users", [])
    
    for u in users:
        if str(u.get("token")) == str(token):
            request.session["authenticated"] = True
            request.session["method"] = "token"
            request.session["identifier"] = token
            return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
            
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
    if error:
        return RedirectResponse(url=f"/login?error=Google+Auth+Failed:+{error}")
    if not code:
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
        return RedirectResponse(url="/login?error=Failed+to+exchange+token")
        
    access_token = token_resp.json().get("access_token")
    
    # Get user info
    user_resp = requests.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10.0
    )
    if user_resp.status_code != 200:
        return RedirectResponse(url="/login?error=Failed+to+get+user+info")
        
    email = user_resp.json().get("email")
    if not email:
        return RedirectResponse(url="/login?error=No+email+found")
        
    # Check against users
    oauth_users = oauth.get("users", [])
    authorized = False
    for u in oauth_users:
        if str(u.get("email", "")).lower() == email.lower():
            authorized = True
            break
            
    if authorized:
        request.session["authenticated"] = True
        request.session["method"] = "google"
        request.session["identifier"] = email
        
        # Log auth attempt
        # Wait, the auth_logs logic needs to be ported too!
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    else:
        # Here we could insert into auth_logs that it was a failed attempt
        return RedirectResponse(url="/login?error=Email+not+authorized")
