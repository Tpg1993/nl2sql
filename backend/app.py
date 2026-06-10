import ast
import os
import time
import re
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

from .auth import get_current_user, verify_password, create_access_token, get_password_hash

# Initialize demo user registry mapping usernames to password hashes, roles, and security contexts
DEMO_USERS = {
    "admin": {
        "password_hash": get_password_hash(os.environ.get("ADMIN_PASSWORD", "admin123")),
        "role": "admin",
        "attributes": {}
    },
    "doctor": {
        "password_hash": get_password_hash("doctor123"),
        "role": "doctor",
        "attributes": {"department_id": 1}
    },
    "researcher": {
        "password_hash": get_password_hash("researcher123"),
        "role": "researcher",
        "attributes": {}
    }
}

def get_select_columns(sql: str) -> list:
    """Helper to parse raw select columns from an SQL query."""
    if not sql:
        return []
    cleaned = " ".join(sql.split())
    select_match = re.search(r"SELECT\s+(.*?)\s+FROM", cleaned, re.IGNORECASE)
    if not select_match:
        return []
    select_clause = select_match.group(1)
    
    cols = []
    current = []
    paren_depth = 0
    for char in select_clause:
        if char == '(':
            paren_depth += 1
        elif char == ')':
            paren_depth -= 1
        
        if char == ',' and paren_depth == 0:
            cols.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    cols.append("".join(current).strip())
    
    resolved = []
    for col in cols:
        col_upper = col.upper()
        if " AS " in col_upper:
            parts = col.split(" AS " if " AS " in col else " as ")
            expr = parts[0].strip()
        else:
            parts = col.split()
            if len(parts) > 1 and parts[-1].isidentifier():
                expr = " ".join(parts[:-1]).strip()
            else:
                expr = col.strip()
        resolved.append(expr)
    return resolved

def apply_policy_masking(parsed_data, sql_query, user_context, agent):
    """Dynamic domain-agnostic masking based on user role and semantic layer policies."""
    if not agent or not agent.semantic_layer:
        return parsed_data
        
    role = user_context.get("role", "researcher")
    policies = agent.semantic_layer.get_security_policies()
    role_policy = policies.get("roles", {}).get(role, {})
    
    if role_policy.get("clearance") == "unrestricted":
        return parsed_data
        
    # If no data or not a collection, return as-is
    if not parsed_data or not isinstance(parsed_data, list):
        return parsed_data

    # Identify queried tables
    queried_tables = []
    for entity_name, details in agent.semantic_layer.entities.items():
        tbl = details.get("table_name")
        if tbl and re.search(r"\b" + re.escape(tbl) + r"\b", sql_query, re.IGNORECASE):
            queried_tables.append(tbl)
            
    # Extract select columns & match classifications
    select_columns = get_select_columns(sql_query)
    col_classifications = []
    for expr in select_columns:
        col_part = expr.split('.')[-1].strip('`"\'')
        classification = None
        for tbl in queried_tables:
            classification = agent.semantic_layer.get_column_classification(tbl, col_part)
            if classification:
                break
        col_classifications.append((col_part, classification))

    # Evaluate ABAC policy if configured for this role
    abac_policies = role_policy.get("abac_policies", [])
    active_abac = None
    for policy in abac_policies:
        ent_context = policy.get("entity_context")
        ent_tbl = agent.semantic_layer.get_table_name(ent_context)
        if ent_tbl in queried_tables:
            active_abac = policy
            break
            
    abac_passed = True
    if active_abac:
        user_attr_name = active_abac["attribute_match"]["user_attribute"]
        user_attr_val = user_context.get("attributes", {}).get(user_attr_name)
        db_col = active_abac["attribute_match"]["db_column"]
        
        # Check if db_col is in select columns
        db_col_idx = -1
        for idx, (col_part, _) in enumerate(col_classifications):
            if col_part.lower() == db_col.lower():
                db_col_idx = idx
                break
                
        # If db_col is selected, we evaluate per-row. Otherwise, check SQL filter
        if db_col_idx == -1:
            filter_pattern = rf"\b{re.escape(db_col)}\b\s*=\s*{re.escape(str(user_attr_val))}"
            in_pattern = rf"\b{re.escape(db_col)}\b\s+in\s*\(\s*{re.escape(str(user_attr_val))}\s*\)"
            if not (re.search(filter_pattern, sql_query, re.IGNORECASE) or re.search(in_pattern, sql_query, re.IGNORECASE)):
                abac_passed = False

    # Perform dynamic masking on rows
    masked_data = []
    
    def apply_mask(val, classification, row_abac_passed):
        if val is None:
            return None
            
        masking_rules = role_policy.get("masking_rules", {})
        rule = masking_rules.get(classification)
        
        # ABAC Fallback Check
        if not row_abac_passed and active_abac and classification in ["pii_name", "pii_dob", "pii_phone", "pii_email"]:
            rule = active_abac.get("fallback_action")
            if rule == "mask":
                rule = "mask_name" if classification == "pii_name" else f"mask_{classification.split('_')[-1]}"
                
        if not rule:
            return val
            
        val_str = str(val)
        if rule == "mask_name":
            parts = val_str.split()
            masked_parts = []
            for p in parts:
                if len(p) > 1:
                    masked_parts.append(p[0] + "*" * (len(p) - 1))
                else:
                    masked_parts.append(p)
            return " ".join(masked_parts)
        elif rule == "mask_phone":
            return "***-***-****"
        elif rule == "mask_email":
            if "@" in val_str:
                u, d = val_str.split("@", 1)
                mu = u[0] + "***" if len(u) > 1 else "u***"
                return f"{mu}@***.com"
            return "e***@***.com"
        elif rule == "mask_dob":
            return "****-**-**"
        elif rule == "redact":
            return "[RESTRICTED]"
        return val

    for row in parsed_data:
        row_abac_passed = abac_passed
        
        # If parsed_data is list of dicts
        if isinstance(row, dict):
            if active_abac and db_col in row:
                if str(row[db_col]) != str(user_attr_val):
                    row_abac_passed = False
            
            new_row = {}
            for k, v in row.items():
                clean_k = k.split('.')[-1].strip('`"\'')
                classification = None
                for tbl in queried_tables:
                    classification = agent.semantic_layer.get_column_classification(tbl, clean_k)
                    if classification:
                        break
                new_row[k] = apply_mask(v, classification, row_abac_passed)
            masked_data.append(new_row)
            
        # If parsed_data is list of lists/tuples
        elif isinstance(row, (list, tuple)):
            row_list = list(row)
            if active_abac and db_col_idx != -1 and db_col_idx < len(row_list):
                if str(row_list[db_col_idx]) != str(user_attr_val):
                    row_abac_passed = False
                    
            for idx in range(len(row_list)):
                if idx < len(col_classifications):
                    _, classification = col_classifications[idx]
                    row_list[idx] = apply_mask(row_list[idx], classification, row_abac_passed)
            masked_data.append(tuple(row_list) if isinstance(row, tuple) else row_list)
        else:
            masked_data.append(row)
            
    return masked_data

ALLOWED_ORIGINS = [origin.strip() for origin in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if origin.strip()]

# Initialize rate limiter
is_testing = os.environ.get("TESTING", "false").lower() == "true"
limiter = Limiter(key_func=get_remote_address, enabled=not is_testing)

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


class OverrideRequest(BaseModel):
    question: str
    corrected_sql: str



@app.post("/api/auth/token")
@limiter.limit("5/minute")
def login(request: Request, login_req: LoginRequest):
    """Authenticate credentials and return a signed JWT token with role claims."""
    print(f"\n[Auth Debug] Attempt: username='{login_req.username}', password='{login_req.password}'")
    
    if login_req.username not in DEMO_USERS:
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    user_info = DEMO_USERS[login_req.username]
    match = verify_password(login_req.password, user_info["password_hash"])
    print(f"[Auth Debug] Match result: {match}")
    if not match:
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    # Inject username, role, and ABAC attributes into JWT payload
    access_token = create_access_token(data={
        "sub": login_req.username,
        "role": user_info["role"],
        "attributes": user_info["attributes"]
    })
    return {
        "access_token": access_token,
        "token_type": "bearer"
    }


@app.post("/api/expert-override")
def add_expert_override(req: OverrideRequest, current_user: dict = Depends(get_current_user)):
    """Allows administrators/experts to override or correct a query translation."""
    if not agent:
        raise HTTPException(status_code=500, detail="Database agent is not initialized.")
    
    # Enforce RBAC: Only Admin can add expert overrides
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=403, 
            detail="Access denied: Only Administrators can create expert query overrides."
        )
        
    if not req.question.strip() or not req.corrected_sql.strip():
        raise HTTPException(status_code=400, detail="Question and corrected SQL cannot be empty.")
    
    try:
        agent.override_store.set_override(req.question, req.corrected_sql)
        if cache_manager:
            import hashlib
            q_hash = hashlib.sha256(req.question.lower().strip().encode("utf-8")).hexdigest()
            cache_manager.delete(q_hash)
        return {"success": True, "message": "Expert query override recorded."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/metadata")
@limiter.limit("30/minute")
def get_metadata(request: Request, current_user: dict = Depends(get_current_user)):
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
def run_query(request: Request, query_req: QueryRequest, current_user: dict = Depends(get_current_user)):
    """Query the agent with a natural language prompt, returning execution latency and conversational response summary."""
    if not agent:
        raise HTTPException(status_code=500, detail="Database agent is not initialized.")
    
    if not query_req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

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
            retrieved_tables = []
            if isinstance(cached_tokens, dict):
                cached_tokens = cached_tokens.copy()
                model = cached_tokens.pop("model", "Unknown Model")
                retries = cached_tokens.pop("retries", 0)
                retrieved_tables = cached_tokens.pop("retrieved_tables", [])
            
            # Apply dynamic masking on cached raw results
            raw_results = cached.get("result")
            masked_results = apply_policy_masking(raw_results, cached.get("query"), current_user, agent)
            
            return {
                "query": cached.get("query"),
                "result": masked_results,
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
                "is_expert_matched": False,
                "lineage": {},
                "estimated_cost": 0.0,
                "retrieved_tables": retrieved_tables,
                "error": None
            }

    # 2. Run agent if cache miss
    try:
        res = agent.query_detailed(query_req.question)
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        raw_result = res["result"]
        conversational_summary = res.get("summary", "")
        
        success = True
        error_message = None
        if isinstance(raw_result, str) and raw_result.startswith("Error executing query:"):
            success = False
            error_message = raw_result
            parsed_data = []
        else:
            try:
                parsed_data = ast.literal_eval(raw_result)
            except Exception:
                parsed_data = raw_result

        # 3. Store in Cache if successfully executed (unmasked raw data)
        if success and cache_manager:
            cached_tokens_payload = {**(res["tokens"] or {})} if res["tokens"] else {}
            cached_tokens_payload["model"] = res.get("model", "gpt-4o-mini")
            cached_tokens_payload["retries"] = res.get("retries", 0)
            cached_tokens_payload["retrieved_tables"] = res.get("retrieved_tables", [])
            
            cache_manager.set(
                question=query_req.question,
                query=res["query"],
                result=parsed_data,
                summary=conversational_summary,
                tokens=cached_tokens_payload
            )

        # Apply dynamic masking on raw parsed data before returning to this specific user
        masked_parsed_data = apply_policy_masking(parsed_data, res["query"], current_user, agent)

        return {
            "query": res["query"],
            "result": masked_parsed_data,
            "summary": conversational_summary,
            "tokens": res["tokens"],
            "success": success,
            "cached": False,
            "latency_ms": elapsed_ms,
            "latency_breakdown": res.get("latency_breakdown", {}),
            "model": res.get("model", "gpt-4o-mini"),
            "retries": res.get("retries", 0),
            "is_expert_matched": res.get("is_expert_matched", False),
            "lineage": res.get("lineage", {}),
            "estimated_cost": res.get("estimated_cost", 0.0),
            "retrieved_tables": res.get("retrieved_tables", []),
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
            "retries": 0,
            "is_expert_matched": False,
            "lineage": {},
            "estimated_cost": 0.0,
            "retrieved_tables": []
        }

