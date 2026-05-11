from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
import secrets
import time

from .auth import load_config
from .routes.auth_routes import router as auth_router
from .routes.page_routes import router as pages_router
from .routes.action_routes import router as actions_router

app = FastAPI(title="Smart Checkout Admin")

# Add Session Middleware
app.add_middleware(
    SessionMiddleware,
    secret_key=secrets.token_urlsafe(32),
    session_cookie="scheck_session",
    max_age=86400 * 7  # 7 days
)

# Simple Rate Limiting Middleware
class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.ip_records = {}
        
    async def dispatch(self, request: Request, call_next):
        # We only rate limit specific endpoints
        if request.url.path in ("/login", "/auth/google/callback", "/action/add", "/action/remove"):
            ip = request.client.host
            now = time.time()
            # Clean up old records
            self.ip_records = {k: v for k, v in self.ip_records.items() if now - v[-1] < 60}
            
            records = self.ip_records.get(ip, [])
            # Filter to last 60 seconds
            records = [t for t in records if now - t < 60]
            
            if len(records) > 30: # Max 30 requests per minute to these sensitive endpoints
                from fastapi.responses import PlainTextResponse
                return PlainTextResponse("Rate limit exceeded.", status_code=429)
                
            records.append(now)
            self.ip_records[ip] = records
            
        return await call_next(request)

app.add_middleware(RateLimitMiddleware)

app.include_router(auth_router)
app.include_router(pages_router)
app.include_router(actions_router)


FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="14" fill="#111827"/>
<path d="M18 20h28v24H18z" fill="none" stroke="#e5e7eb" stroke-width="4"/>
<path d="M24 26h4v4h-4zm12 0h4v4h-4zM24 36h4v4h-4zm12 0h4v4h-4z" fill="#00cec9"/>
</svg>"""


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(
        content=FAVICON_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


def _error_page(title: str, code: int, message: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} - Smart Checkout</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0f1117; --surface: rgba(28,31,46,0.7); --text: #e4e6f0;
      --muted: #7c829d; --accent: #6c5ce7; --accent-glow: rgba(108,92,231,0.25);
      --line: rgba(255,255,255,0.06); --radius: 12px;
    }}
    [data-theme="light"] {{
      color-scheme: light;
      --bg: #f9fafb; --surface: rgba(255,255,255,0.7); --text: #111827;
      --muted: #6b7280; --accent: #4f46e5; --accent-glow: rgba(79,70,229,0.25);
      --line: rgba(0,0,0,0.1);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; background: var(--bg); color: var(--text);
      font-family: "Inter", -apple-system, sans-serif;
      display: flex; align-items: center; justify-content: center; min-height: 100vh;
    }}
    .error-card {{
      text-align: center; padding: 48px; background: var(--surface);
      backdrop-filter: blur(16px); border: 1px solid var(--line);
      border-radius: var(--radius); max-width: 420px; width: 100%;
    }}
    .error-code {{
      font-size: 72px; font-weight: 700; color: var(--accent);
      line-height: 1; margin-bottom: 8px;
      text-shadow: 0 0 30px var(--accent-glow);
    }}
    .error-title {{ font-size: 20px; font-weight: 600; margin-bottom: 12px; }}
    .error-msg {{ color: var(--muted); font-size: 14px; margin-bottom: 24px; }}
    .back-link {{
      display: inline-flex; align-items: center; gap: 8px;
      padding: 10px 24px; background: var(--accent); color: #fff;
      border-radius: 8px; text-decoration: none; font-weight: 500;
      transition: all 0.2s ease;
    }}
    .back-link:hover {{ transform: translateY(-2px); box-shadow: 0 6px 16px var(--accent-glow); }}
  </style>
  <script>
    const t = localStorage.getItem('scheck_theme');
    if (t === 'light' || (!t && window.matchMedia('(prefers-color-scheme: light)').matches))
      document.documentElement.setAttribute('data-theme', 'light');
  </script>
</head>
<body>
  <div class="error-card">
    <div class="error-code">{code}</div>
    <div class="error-title">{title}</div>
    <div class="error-msg">{message}</div>
    <a href="/" class="back-link">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 18 9 12 15 6"></polyline></svg>
      Go to Dashboard
    </a>
  </div>
</body>
</html>"""


@app.exception_handler(403)
async def forbidden_handler(request: Request, exc):
    return HTMLResponse(
        content=_error_page("Forbidden", 403, "You don't have permission to access this resource."),
        status_code=403
    )

@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return HTMLResponse(
        content=_error_page("Not Found", 404, "The page you're looking for doesn't exist or has been moved."),
        status_code=404
    )
