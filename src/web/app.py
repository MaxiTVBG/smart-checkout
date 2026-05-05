from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
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

@app.exception_handler(403)
async def forbidden_handler(request: Request, exc):
    return HTMLResponse(
        content="<h1 style='color:red; font-family:sans-serif; text-align:center; margin-top:50px;'>403 Forbidden</h1>",
        status_code=403
    )

@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return HTMLResponse(
        content="<h1 style='color:#666; font-family:sans-serif; text-align:center; margin-top:50px;'>404 Not Found</h1>",
        status_code=404
    )
