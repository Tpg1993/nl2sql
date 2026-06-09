import ast
import os
import time
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import inspect
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from .agent import EHRQueryAgent
from .cache import get_cache_manager
from .auth import get_current_user, verify_password, create_access_token

# Load environment configuration
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_PLAIN = os.environ.get("ADMIN_PASSWORD", "admin123")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH")

if not ADMIN_PASSWORD_HASH:
    from .auth import get_password_hash
    ADMIN_PASSWORD_HASH = get_password_hash(ADMIN_PASSWORD_PLAIN)

ALLOWED_ORIGINS = [origin.strip() for origin in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if origin.strip()]

# Initialize rate limiter
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="EHR SQL Agent API",
    description="API server for natural language querying of EHR data using LangGraph and LangChain.",
    version="1.0.0"
)

# Enable CORS (restricted to ALLOWED_ORIGINS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Initialize agent
try:
    agent = EHRQueryAgent()
except Exception as e:
    print(f"Error initializing agent: {e}")
    agent = None

# Initialize cache manager (defaults to 1 hour TTL)
try:
    cache_manager = get_cache_manager(ttl_seconds=3600)
except Exception as e:
    print(f"Error initializing cache manager: {e}")
    cache_manager = None


class QueryRequest(BaseModel):
    question: str


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/auth/token")
@limiter.limit("5/minute")
def login(request: Request, login_req: LoginRequest):
    """Authenticate credentials and return a signed JWT token."""
    print(f"\n[Auth Debug] Attempt: username='{login_req.username}', password='{login_req.password}'")
    print(f"[Auth Debug] Expected: username='{ADMIN_USERNAME}', hash='{ADMIN_PASSWORD_HASH}'")
    match = verify_password(login_req.password, ADMIN_PASSWORD_HASH)
    print(f"[Auth Debug] Match result: {match}")
    if login_req.username != ADMIN_USERNAME or not match:
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(data={"sub": login_req.username})
    return {
        "access_token": access_token,
        "token_type": "bearer"
    }


@app.get("/api/metadata")
@limiter.limit("30/minute")
def get_metadata(request: Request, current_user: str = Depends(get_current_user)):
    """Retrieve database metadata (tables and columns) for UI sidebar."""
    if not agent:
        raise HTTPException(status_code=500, detail="Database agent is not initialized.")
    
    try:
        inspector = inspect(agent.engine)
        metadata = {}
        for table_name in agent.db.get_usable_table_names():
            columns = [col["name"] for col in inspector.get_columns(table_name)]
            metadata[table_name] = columns
        return {
            "dialect": agent.db.dialect,
            "schema": metadata,
            "success": True
        }
    except Exception as e:
        return {
            "error": str(e),
            "success": False
        }


@app.post("/api/query")
@limiter.limit("15/minute")
def run_query(request: Request, query_req: QueryRequest, current_user: str = Depends(get_current_user)):
    """Query the agent with a natural language prompt, returning execution latency and conversational response summary."""
    if not agent:
        raise HTTPException(status_code=500, detail="Database agent is not initialized.")
    
    if not query_req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    # Enforce input length constraint to mitigate Prompt Injection / Denial of Service
    if len(query_req.question.strip()) > 500:
        raise HTTPException(
            status_code=400, 
            detail="Question exceeds maximum permitted length of 500 characters."
        )

    start_time = time.perf_counter()

    # 1. Attempt to serve from Cache
    if cache_manager:
        cached = cache_manager.get(query_req.question)
        if cached:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            cached_tokens = cached.get("tokens") or {}
            model = "Unknown Model"
            retries = 0
            if isinstance(cached_tokens, dict):
                # Make a shallow copy before popping to avoid side-effects
                cached_tokens = cached_tokens.copy()
                model = cached_tokens.pop("model", "Unknown Model")
                retries = cached_tokens.pop("retries", 0)
            
            return {
                "query": cached.get("query"),
                "result": cached.get("result"),
                "summary": cached.get("summary", ""),
                "tokens": cached_tokens if cached_tokens else None,
                "success": True,
                "cached": True,
                "latency_ms": elapsed_ms,
                "latency_breakdown": {
                    "cache": elapsed_ms,
                    "schema": 0.0,
                    "generation": 0.0,
                    "execution": 0.0,
                    "summarization": 0.0
                },
                "model": model,
                "retries": retries,
                "error": None
            }

    # 2. Run agent if cache miss
    try:
        res = agent.query_detailed(query_req.question)
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        raw_result = res["result"]
        conversational_summary = res.get("summary", "")
        
        # Check if the execution returned a query error
        success = True
        error_message = None
        if isinstance(raw_result, str) and raw_result.startswith("Error executing query:"):
            success = False
            error_message = raw_result
            parsed_data = []
        else:
            # Safely parse the SQL output string into Python structures (e.g. list of tuples/lists)
            try:
                # ast.literal_eval is safe for parsing string representations of standard python types
                parsed_data = ast.literal_eval(raw_result)
            except Exception:
                # Fallback to string if it cannot be parsed
                parsed_data = raw_result

        # 3. Store in Cache if successfully executed
        if success and cache_manager:
            cached_tokens_payload = {**(res["tokens"] or {})} if res["tokens"] else {}
            cached_tokens_payload["model"] = res.get("model", "gpt-4o-mini")
            cached_tokens_payload["retries"] = res.get("retries", 0)
            
            cache_manager.set(
                question=query_req.question,
                query=res["query"],
                result=parsed_data,
                summary=conversational_summary,
                tokens=cached_tokens_payload
            )

        return {
            "query": res["query"],
            "result": parsed_data,
            "summary": conversational_summary,
            "tokens": res["tokens"],
            "success": success,
            "cached": False,
            "latency_ms": elapsed_ms,
            "latency_breakdown": res.get("latency_breakdown", {}),
            "model": res.get("model", "gpt-4o-mini"),
            "retries": res.get("retries", 0),
            "error": error_message
        }
    except Exception as e:
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return {
            "error": str(e),
            "success": False,
            "latency_ms": elapsed_ms,
            "latency_breakdown": {
                "cache": 0.0,
                "schema": 0.0,
                "generation": 0.0,
                "execution": 0.0,
                "summarization": 0.0
            },
            "model": "Unknown Model",
            "retries": 0
        }
