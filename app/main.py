from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.database import init_db
from app.routers import auth, documents, concepts, intelligence
from app.core.rate_limiter import limiter
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

import asyncio
from app.services.ingestion import recover_interrupted_ingestions

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup actions
    await init_db()
    
    # Run recovery check in the background after a brief delay
    async def trigger_recovery():
        await asyncio.sleep(1)
        await recover_interrupted_ingestions()
        
    asyncio.create_task(trigger_recovery())
    
    yield
    # Shutdown actions (none needed)

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    lifespan=lifespan
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Configure CORS for Next.js frontend development and production
origins = [o.strip().rstrip("/") for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# MCP Authentication Middleware & Mounting
from app.services.mcp_server import mcp
from app.core.security import verify_mcp_session_or_pat
from fastapi import Request
from fastapi.responses import JSONResponse

@app.middleware("http")
async def verify_mcp_auth(request: Request, call_next):
    # Only verify auth for the MCP routes
    if request.url.path.startswith("/api/mcp"):
        auth_header = request.headers.get("authorization")
        query_token = request.query_params.get("token") or request.query_params.get("authorization")
        
        token = None
        if auth_header:
            token = auth_header
        elif query_token:
            token = query_token
            
        if not token:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing authorization credentials for Flint MCP server."}
            )
            
        try:
            user = await verify_mcp_session_or_pat(token)
            request.state.user = user
        except Exception as e:
            return JSONResponse(
                status_code=401,
                content={"detail": f"Unauthorized: {str(e)}"}
            )
            
    response = await call_next(request)
    return response

# Mount the MCP server streamable HTTP sub-application
app.mount("/api/mcp", mcp.streamable_http_app())

# Register endpoints under "/api" prefix
app.include_router(auth.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(concepts.router, prefix="/api")
app.include_router(intelligence.router, prefix="/api")

@app.get("/")
def root():
    return {
        "message": "Flint API is running",
        "status": "online"
    }

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "project": settings.PROJECT_NAME,
        "version": settings.VERSION
    }
