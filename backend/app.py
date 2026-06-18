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
        "password_hash": get_password_hash(os.environ.get("DOCTOR_PASSWORD", "doctor123")),
        "role": "doctor",
        "attributes": {"department_id": 1}
    },
    "researcher": {
        "password_hash": get_password_hash(os.environ.get("RESEARCHER_PASSWORD", "researcher123")),
        "role": "researcher",
        "attributes": {}
    }
}

# Add alias for custom administrator username if configured in environment
admin_env_username = os.environ.get("ADMIN_USERNAME")
if admin_env_username and admin_env_username != "admin":
    DEMO_USERS[admin_env_username] = {
        "password_hash": get_password_hash(os.environ.get("ADMIN_PASSWORD", "admin123")),
        "role": "admin",
        "attributes": {}
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
        parts = re.split(r'\bAS\b', col, flags=re.IGNORECASE)
        if len(parts) > 1:
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
    select_columns_raw = []
    if sql_query:
        cleaned = " ".join(sql_query.split())
        select_match = re.search(r"SELECT\s+(.*?)\s+FROM", cleaned, re.IGNORECASE)
        if select_match:
            select_clause = select_match.group(1)
            current = []
            paren_depth = 0
            for char in select_clause:
                if char == '(':
                    paren_depth += 1
                elif char == ')':
                    paren_depth -= 1
                if char == ',' and paren_depth == 0:
                    select_columns_raw.append("".join(current).strip())
                    current = []
                else:
                    current.append(char)
            select_columns_raw.append("".join(current).strip())

    col_classifications_map = {}
    col_classifications = []
    
    for col in select_columns_raw:
        if not col:
            continue
        parts = re.split(r'\bAS\b', col, flags=re.IGNORECASE)
        if len(parts) > 1:
            expr = parts[0].strip()
            alias = parts[1].strip().strip('`"\'')
        else:
            space_parts = col.split()
            if len(space_parts) > 1 and space_parts[-1].isidentifier():
                expr = " ".join(space_parts[:-1]).strip()
                alias = space_parts[-1].strip().strip('`"\'')
            else:
                expr = col.strip()
                alias = None
                
        expr_clean = expr.split('.')[-1].strip('`"\'')
        alias_clean = alias.split('.')[-1].strip('`"\'') if alias else None
        
        classification = None
        for tbl in queried_tables:
            classification = agent.semantic_layer.get_column_classification(tbl, expr_clean)
            if classification:
                break
                
        col_classifications.append((expr_clean, classification))
        
        for key in [expr, expr_clean, alias, alias_clean]:
            if key:
                col_classifications_map[key.lower()] = classification

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
            row_db_val = None
            found_db_col = False
            if active_abac:
                for k, v in row.items():
                    clean_k = k.split('.')[-1].strip('`"\'').lower()
                    if clean_k == db_col.lower():
                        row_db_val = v
                        found_db_col = True
                        break
            if active_abac and found_db_col:
                if str(row_db_val) != str(user_attr_val):
                    row_abac_passed = False
            
            new_row = {}
            for k, v in row.items():
                classification = col_classifications_map.get(k.lower())
                if not classification:
                    # Fallback to direct lookup
                    clean_k = k.split('.')[-1].strip('`"\'').lower()
                    classification = col_classifications_map.get(clean_k)
                if not classification:
                    clean_k = k.split('.')[-1].strip('`"\'')
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

# Initialize cache manager (defaults to 1 hour TTL)
try:
    cache_manager = get_cache_manager(ttl_seconds=3600)
except Exception as e:
    print(f"Error initializing cache manager: {e}")
    cache_manager = None

# Initialize agent
try:
    agent = EHRQueryAgent(cache_manager=cache_manager)
except Exception as e:
    print(f"Error initializing agent: {e}")
    agent = None

# Initialize cryptographic audit ledger
try:
    from .audit_ledger import AuditLedger
    audit_ledger = AuditLedger()
except Exception as e:
    print(f"Error initializing audit ledger: {e}")
    audit_ledger = None


class QueryRequest(BaseModel):
    question: str
    thread_id: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


class OverrideRequest(BaseModel):
    question: str
    corrected_sql: str
    username: str | None = None



@app.post("/api/auth/token")
@limiter.limit("100/minute")
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
        agent.override_store.set_override(req.question, req.corrected_sql, username=req.username)
        if cache_manager:
            import hashlib
            q_hash = hashlib.sha256(req.question.lower().strip().encode("utf-8")).hexdigest()
            cache_manager.delete(q_hash)
        return {"success": True, "message": "Expert query override recorded."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class SemanticLayerConfigPayload(BaseModel):
    version: str
    entities: dict
    relationships: list
    metrics: dict
    security_policies: dict
    few_shot_examples: list = []


@app.get("/api/config/semantic-layer")
def get_semantic_layer_config(current_user: dict = Depends(get_current_user)):
    """Retrieves the current semantic layer configuration alongside database table/column metadata."""
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Access denied: Only Administrators can view configuration settings."
        )
    
    if not agent:
        raise HTTPException(status_code=500, detail="Database agent is not initialized.")
        
    try:
        import yaml
        base_dir = os.path.dirname(os.path.abspath(__file__))
        yaml_path = os.path.join(base_dir, "semantic_layer.yaml")
        with open(yaml_path, "r", encoding="utf-8") as f:
            config_data = yaml.safe_load(f)
            
        inspector = inspect(agent.engine)
        db_metadata = {}
        for table_name in agent.db.get_usable_table_names():
            columns = [col["name"] for col in inspector.get_columns(table_name)]
            db_metadata[table_name] = columns
            
        return {
            "success": True,
            "config": config_data,
            "db_metadata": db_metadata
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/config/semantic-layer")
def update_semantic_layer_config(payload: SemanticLayerConfigPayload, current_user: dict = Depends(get_current_user)):
    """Validates and updates the semantic layer configuration file, reloading it in-memory."""
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Access denied: Only Administrators can modify configuration settings."
        )
        
    if not agent:
        raise HTTPException(status_code=500, detail="Database agent is not initialized.")
        
    try:
        import yaml
        
        # 1. Fetch valid tables and columns from the database for validation
        inspector = inspect(agent.engine)
        valid_schema = {}
        for table_name in agent.db.get_usable_table_names():
            valid_schema[table_name] = [col["name"] for col in inspector.get_columns(table_name)]
            
        # 2. Validation Checks
        # Validate entities
        for ent_name, ent_data in payload.entities.items():
            tbl_name = ent_data.get("table_name")
            if not tbl_name:
                raise HTTPException(status_code=400, detail=f"Entity '{ent_name}' is missing table_name.")
            if tbl_name not in valid_schema:
                raise HTTPException(status_code=400, detail=f"Table '{tbl_name}' mapped to entity '{ent_name}' does not exist in database.")
                
            pk = ent_data.get("primary_key")
            if pk and pk not in valid_schema[tbl_name]:
                raise HTTPException(status_code=400, detail=f"Primary key '{pk}' in entity '{ent_name}' does not exist in table '{tbl_name}'.")
                
            fields = ent_data.get("fields", {})
            for logical_f, physical_f in fields.items():
                col_name = physical_f.get("column_name") if isinstance(physical_f, dict) else physical_f
                if col_name not in valid_schema[tbl_name]:
                    raise HTTPException(status_code=400, detail=f"Field '{logical_f}' maps to non-existent column '{col_name}' in table '{tbl_name}'.")

        # Validate relationships
        for rel in payload.relationships:
            from_ent = rel.get("from_entity")
            to_ent = rel.get("to_entity")
            if from_ent not in payload.entities:
                raise HTTPException(status_code=400, detail=f"Relationship has non-existent source entity '{from_ent}'.")
            if to_ent not in payload.entities:
                raise HTTPException(status_code=400, detail=f"Relationship has non-existent destination entity '{to_ent}'.")
                
            from_tbl = payload.entities[from_ent]["table_name"]
            to_tbl = payload.entities[to_ent]["table_name"]
            
            join_keys = rel.get("join_keys", {})
            from_key = join_keys.get("from_key")
            to_key = join_keys.get("to_key")
            
            if from_key not in valid_schema[from_tbl]:
                raise HTTPException(status_code=400, detail=f"Join key '{from_key}' does not exist in table '{from_tbl}'.")
            if to_key not in valid_schema[to_tbl]:
                raise HTTPException(status_code=400, detail=f"Join key '{to_key}' does not exist in table '{to_tbl}'.")

        # 3. Write back to file
        base_dir = os.path.dirname(os.path.abspath(__file__))
        yaml_path = os.path.join(base_dir, "semantic_layer.yaml")
        
        # Convert Pydantic payload to Python dict
        config_dict = payload.model_dump()
        
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False)
            
        # 4. Hot-reload agent's semantic layer, Metadata RAG, and FewShotLibrary in-memory
        from .semantic_layer import SemanticLayer
        from .metadata_rag import MetadataRAG
        from .few_shot_library import FewShotLibrary
        agent.semantic_layer = SemanticLayer(yaml_path)
        agent.metadata_rag = MetadataRAG(agent.engine, yaml_path)
        agent.few_shot_library = FewShotLibrary(yaml_path)
        
        # Clear database cache to prevent returning stale cached results
        if cache_manager:
            try:
                cache_manager.clear()
            except Exception as e:
                print(f"[Config Editor] Warning: Failed to clear cache: {e}")
        
        return {"success": True, "message": "Semantic layer configuration saved and hot-reloaded successfully."}
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class TestMetricPayload(BaseModel):
    formula: str


@app.post("/api/config/test-metric")
def test_metric_formula(payload: TestMetricPayload, current_user: dict = Depends(get_current_user)):
    """Dry-runs a metric SQL formula against the active database connection to verify syntax."""
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Access denied: Only Administrators can test metric queries."
        )
    if not agent:
        raise HTTPException(status_code=500, detail="Database agent is not initialized.")
        
    formula = payload.formula.strip()
    if not formula:
        raise HTTPException(status_code=400, detail="Metric formula cannot be empty.")
        
    try:
        agent.audit_sql_query(formula)
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
        
    try:
        from sqlalchemy import text
        with agent.engine.connect() as conn:
            try:
                # Try EXPLAIN first
                conn.execute(text(f"EXPLAIN {formula}"))
            except Exception:
                # Fallback to SELECT * FROM (<formula>) LIMIT 0
                conn.execute(text(f"SELECT * FROM ({formula}) LIMIT 0"))
        return {"success": True, "message": "Metric formula query compiled successfully."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/config/discover")
def discover_schema_config(current_user: dict = Depends(get_current_user)):
    """Inspects the active database schema across all registered engines to auto-discover entities and joins."""
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Access denied: Only Administrators can auto-discover database schema mappings."
        )
    if not agent:
        raise HTTPException(status_code=500, detail="Database agent is not initialized.")
        
    try:
        discovered_entities = {}
        discovered_relationships = []
        
        def clean_entity_name(tbl: str) -> str:
            name = tbl.lower()
            if name.endswith("ies"):
                name = name[:-3] + "y"
            elif name.endswith("ses"):
                name = name[:-2] + "is"  # diagnoses -> diagnosis
            elif name.endswith("es") and not name.endswith("ces"):
                name = name[:-2]
            elif name.endswith("s") and not name.endswith("ss"):
                name = name[:-1]
            return "".join(part.capitalize() for part in name.split("_"))
            
        from backend.agent import TABLE_DB_MAPPINGS
        
        # Iterate over all registered database engines
        for db_name, engine in agent.db_engines.items():
            try:
                inspector = inspect(engine)
                for table_name in inspector.get_table_names():
                    # Check if this table is expected in the current DB mapping (or default fallback)
                    # to prevent duplicate entities from fallbacks
                    expected_db = TABLE_DB_MAPPINGS.get(table_name, "db_local_ehr")
                    if expected_db != db_name:
                        continue
                        
                    ent_name = clean_entity_name(table_name)
                    
                    # Fetch columns and types
                    cols_info = inspector.get_columns(table_name)
                    
                    # Guess Primary Key
                    pk_info = inspector.get_pk_constraint(table_name)
                    pk_cols = pk_info.get("constrained_columns", [])
                    primary_key = pk_cols[0] if pk_cols else ""
                    
                    if not primary_key:
                        col_names_lower = [c["name"].lower() for c in cols_info]
                        if "id" in col_names_lower:
                            primary_key = cols_info[col_names_lower.index("id")]["name"]
                        else:
                            for col in cols_info:
                                cname = col["name"].lower()
                                if cname.endswith("_id") or cname == f"{table_name.rstrip('s').lower()}_id":
                                    primary_key = col["name"]
                                    break
                    
                    fields = {}
                    for col in cols_info:
                        cname = col["name"]
                        cname_lower = cname.lower()
                        
                        # Guess classification
                        classification = "none"
                        if "name" in cname_lower:
                            classification = "pii_name"
                        elif "dob" in cname_lower or "birth" in cname_lower:
                            classification = "pii_dob"
                        elif "phone" in cname_lower or "cell" in cname_lower or "mobile" in cname_lower:
                            classification = "pii_phone"
                        elif "email" in cname_lower or "mail" in cname_lower:
                            classification = "pii_email"
                        elif any(kw in cname_lower for kw in ["charge", "cost", "amount", "price", "fee"]):
                            classification = "financial"
                            
                        logical_f = "".join(part if idx == 0 else part.capitalize() for idx, part in enumerate(cname.split("_")))
                        
                        if classification == "none":
                            fields[logical_f] = cname
                        else:
                            fields[logical_f] = {
                                "column_name": cname,
                                "classification": classification
                            }
                            
                    discovered_entities[ent_name] = {
                        "table_name": table_name,
                        "primary_key": primary_key,
                        "description": f"Auto-discovered mappings for physical table '{table_name}'.",
                        "fields": fields
                    }
                    
                    # Discover Relationships via Foreign Keys
                    fkeys = inspector.get_foreign_keys(table_name)
                    for fk in fkeys:
                        ref_table = fk.get("referred_table")
                        constrained_cols = fk.get("constrained_columns", [])
                        referred_cols = fk.get("referred_columns", [])
                        
                        if constrained_cols and referred_cols and ref_table:
                            discovered_relationships.append({
                                "from_entity": ent_name,
                                "to_entity": clean_entity_name(ref_table),
                                "join_type": "many_to_one",
                                "join_keys": {
                                    "from_key": constrained_cols[0],
                                    "to_key": referred_cols[0]
                                }
                            })
            except Exception as engine_err:
                print(f"Skipping auto-discovery for database {db_name}: {engine_err}")
        
        return {
            "success": True,
            "entities": discovered_entities,
            "relationships": discovered_relationships
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/config/audit-logs")
def get_audit_logs(current_user: dict = Depends(get_current_user)):
    """Retrieves all immutable audit logs, restricted to administrators."""
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Access denied: Only Administrators can view audit logs."
        )
    if not audit_ledger:
        raise HTTPException(status_code=500, detail="Audit ledger is not initialized.")
    try:
        logs = audit_ledger.get_all_logs()
        return {"success": True, "logs": logs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/config/verify-audit-ledger")
def verify_audit_ledger_endpoint(current_user: dict = Depends(get_current_user)):
    """Verifies the integrity of the audit ledger hash chain, restricted to administrators."""
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Access denied: Only Administrators can verify the audit ledger."
        )
    if not audit_ledger:
        raise HTTPException(status_code=500, detail="Audit ledger is not initialized.")
    try:
        verified, tampered_ids = audit_ledger.verify_ledger_integrity()
        return {
            "success": True,
            "verified": verified,
            "tampered_ids": tampered_ids,
            "message": "Audit ledger integrity verified successfully." if verified else "Audit ledger tampering detected!"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/config/git-info")
def get_git_info(current_user: dict = Depends(get_current_user)):
    """Retrieves current Git status, active branch, and list of branches, restricted to administrators."""
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Access denied: Only Administrators can view git information."
        )
    try:
        import subprocess
        # Get active branch
        active_branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            text=True
        ).strip()
        
        # Get list of branches (local & remote)
        branch_output = subprocess.check_output(
            ["git", "branch", "-a"],
            text=True
        )
        branches = []
        for line in branch_output.splitlines():
            line = line.strip()
            if not line:
                continue
            name = line.lstrip("* ").strip()
            if name.startswith("remotes/origin/"):
                name = name[len("remotes/origin/"):]
            if name == "HEAD" or "origin/HEAD" in name:
                continue
            if name not in branches:
                branches.append(name)
                
        # Check if GITHUB_TOKEN is present in env
        github_token = os.environ.get("GITHUB_TOKEN")
        has_token = bool(github_token and github_token.strip() and not github_token.startswith("ghp_your_"))
        
        return {
            "success": True,
            "active_branch": active_branch,
            "branches": branches,
            "has_token": has_token
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Git metadata retrieval failed: {str(e)}")


class GitOpsPRPayload(BaseModel):
    target_branch: str
    pr_title: str
    pr_description: str


@app.post("/api/config/gitops/pr-sync")
def gitops_pr_sync(payload: GitOpsPRPayload, current_user: dict = Depends(get_current_user)):
    """Stages, commits, pushes current semantic layer config, and creates a GitHub Pull Request."""
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Access denied: Only Administrators can execute GitOps syncs."
        )
    
    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token or not github_token.strip() or github_token.startswith("ghp_your_"):
        raise HTTPException(
            status_code=400,
            detail="GITHUB_TOKEN is missing or not configured in backend/.env. Please define it to execute PR Sync."
        )
        
    import subprocess
    import urllib.request
    import urllib.error
    import json
    
    # 1. Parse Git remote origin URL to find owner & repo
    try:
        remote_url = subprocess.check_output(["git", "remote", "get-url", "origin"], text=True).strip()
        if remote_url.endswith(".git"):
            remote_url = remote_url[:-4]
        if "github.com" in remote_url:
            parts = remote_url.split("github.com")[-1].lstrip(":").lstrip("/").split("/")
            if len(parts) >= 2:
                owner, repo = parts[0], parts[1]
            else:
                raise Exception("Could not parse owner and repository from remote URL.")
        else:
            raise Exception("Git remote origin does not point to a GitHub repository.")
    except Exception as ge:
        raise HTTPException(status_code=500, detail=f"Failed to identify GitHub repository details: {str(ge)}")
        
    try:
        # 2. Get active branch
        active_branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            text=True
        ).strip()
        
        if active_branch == payload.target_branch:
            raise HTTPException(
                status_code=400,
                detail=f"Target branch cannot be equal to the active branch ({active_branch})."
            )
            
        # 3. Check for modification changes on backend/semantic_layer.yaml
        status_output = subprocess.check_output(
            ["git", "status", "--porcelain", "backend/semantic_layer.yaml"],
            text=True
        ).strip()
        
        if status_output:
            subprocess.check_call(["git", "add", "backend/semantic_layer.yaml"])
            subprocess.check_call(["git", "commit", "-m", f"chore: update semantic layer configuration via console ({payload.pr_title})"])
            
        # 4. Push active branch to remote
        subprocess.check_call(["git", "push", "origin", active_branch])
        
        # 5. Create Pull Request via GitHub API
        api_url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
        data = {
            "title": payload.pr_title,
            "body": payload.pr_description or "GitOps auto-sync of Semantic Layer from Admin dashboard.",
            "head": active_branch,
            "base": payload.target_branch
        }
        
        req = urllib.request.Request(
            api_url,
            data=json.dumps(data).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "FastAPI-GitOps-Agent",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        
        try:
            with urllib.request.urlopen(req) as response:
                res_json = json.loads(response.read().decode("utf-8"))
                return {
                    "success": True,
                    "pr_url": res_json.get("html_url"),
                    "message": "Pull Request successfully created!"
                }
        except urllib.error.HTTPError as he:
            err_body = he.read().decode("utf-8")
            try:
                err_json = json.loads(err_body)
                errors = err_json.get("errors", [])
                for err in errors:
                    msg = err.get("message", "")
                    if "A pull request already exists" in msg:
                        return {
                            "success": True,
                            "pr_url": f"https://github.com/{owner}/{repo}/pulls",
                            "message": "Latest configuration changes pushed. Pull Request already exists and was updated."
                        }
                raise HTTPException(status_code=400, detail=err_json.get("message", err_body))
            except HTTPException as h_exc:
                raise h_exc
            except Exception:
                raise HTTPException(status_code=400, detail=err_body)
    except HTTPException as he:
        raise he
    except subprocess.CalledProcessError as cpe:
        raise HTTPException(
            status_code=500,
            detail=f"Git command failed: {cpe.output if hasattr(cpe, 'output') else str(cpe)}"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"GitOps execution error: {str(e)}")


@app.get("/api/metadata")
@limiter.limit("200/minute")
def get_metadata(request: Request, current_user: dict = Depends(get_current_user)):
    """Retrieve database metadata (tables and columns) across all connections for UI sidebar."""
    if not agent:
        raise HTTPException(status_code=500, detail="Database agent is not initialized.")
    
    try:
        from backend.agent import TABLE_DB_MAPPINGS
        metadata = {}
        # Scan each registered engine in the pool
        for db_name, engine in agent.db_engines.items():
            try:
                inspector = inspect(engine)
                for table_name in inspector.get_table_names():
                    # Check if this table is expected in this database target to prevent duplicate column listing
                    expected_db = TABLE_DB_MAPPINGS.get(table_name, "db_local_ehr")
                    if expected_db == db_name:
                        columns = [col["name"] for col in inspector.get_columns(table_name)]
                        metadata[table_name] = columns
            except Exception as engine_err:
                print(f"Skipping metadata reflection for engine {db_name}: {engine_err}")
                
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
@limiter.limit("100/minute")
def run_query(request: Request, query_req: QueryRequest, current_user: dict = Depends(get_current_user)):
    """Query the agent with a natural language prompt, returning execution latency and conversational response summary."""
    if not agent:
        raise HTTPException(status_code=500, detail="Database agent is not initialized.")
        
    # Under test mode or dynamic development, reload configs on each query to keep in-sync with restored yaml backups
    if True:
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            yaml_path = os.path.join(base_dir, "semantic_layer.yaml")
            from .semantic_layer import SemanticLayer
            from .metadata_rag import MetadataRAG
            from .few_shot_library import FewShotLibrary
            agent.semantic_layer = SemanticLayer(yaml_path)
            agent.metadata_rag = MetadataRAG(agent.engine, yaml_path)
            agent.few_shot_library = FewShotLibrary(yaml_path)
        except Exception as reload_err:
            print(f"[Testing Hot-Reload] Failed to reload configurations: {reload_err}")
    
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
            rag_savings_pct = 0.0
            retrieved_few_shots = []
            scan_breakdown = []
            optimizer_advisories = []
            raw_plan = ""
            estimated_cost = 0.0
            if isinstance(cached_tokens, dict):
                cached_tokens = cached_tokens.copy()
                model = cached_tokens.pop("model", "Unknown Model") or "Unknown Model"
                retries = cached_tokens.pop("retries", 0) or 0
                retrieved_tables = cached_tokens.pop("retrieved_tables", []) or []
                rag_savings_pct = cached_tokens.pop("rag_savings_pct", 0.0) or 0.0
                retrieved_few_shots = cached_tokens.pop("retrieved_few_shots", []) or []
                scan_breakdown = cached_tokens.pop("scan_breakdown", []) or []
                optimizer_advisories = cached_tokens.pop("optimizer_advisories", []) or []
                raw_plan = cached_tokens.pop("raw_plan", "") or ""
                estimated_cost = cached_tokens.pop("estimated_cost", 0.0) or 0.0
            
            # Apply dynamic masking on cached raw results
            raw_results = cached.get("result")
            masked_results = apply_policy_masking(raw_results, cached.get("query"), current_user, agent)
            
            # Record audit log
            if audit_ledger:
                try:
                    audit_ledger.write_audit_log(
                        username=current_user.get("username", "anonymous"),
                        role=current_user.get("role", "researcher"),
                        prompt=query_req.question,
                        sql_query=cached.get("query", ""),
                        latency_ms=elapsed_ms,
                        dataset=masked_results
                    )
                except Exception as audit_err:
                    print(f"Audit logging failed on cache hit: {audit_err}")
            
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
                "estimated_cost": estimated_cost,
                "retrieved_tables": retrieved_tables,
                "rag_savings_pct": rag_savings_pct,
                "retrieved_few_shots": retrieved_few_shots,
                "scan_breakdown": scan_breakdown,
                "optimizer_advisories": optimizer_advisories,
                "raw_plan": raw_plan,
                "error": None
            }


    # 2. Run agent if cache miss
    try:
        res = agent.query_detailed(query_req.question, user_context=current_user, thread_id=query_req.thread_id)
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        raw_result = res["result"]
        conversational_summary = res.get("summary", "")
        
        success = True
        error_message = None
        if isinstance(raw_result, str) and raw_result.startswith("Error executing query:"):
            if "Security violation" in raw_result or "Cost violation" in raw_result:
                success = True
                parsed_data = raw_result
            else:
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
            cached_tokens_payload["rag_savings_pct"] = res.get("rag_savings_pct", 0.0)
            cached_tokens_payload["retrieved_few_shots"] = res.get("retrieved_few_shots", [])
            cached_tokens_payload["scan_breakdown"] = res.get("scan_breakdown", [])
            cached_tokens_payload["optimizer_advisories"] = res.get("optimizer_advisories", [])
            cached_tokens_payload["raw_plan"] = res.get("raw_plan", "")
            cached_tokens_payload["estimated_cost"] = res.get("estimated_cost", 0.0)
            
            cache_manager.set(
                question=query_req.question,
                query=res["query"],
                result=parsed_data,
                summary=conversational_summary,
                tokens=cached_tokens_payload
            )

        # Apply dynamic masking on raw parsed data before returning to this specific user
        masked_parsed_data = apply_policy_masking(parsed_data, res["query"], current_user, agent)

        # Record audit log
        if audit_ledger:
            try:
                audit_ledger.write_audit_log(
                    username=current_user.get("username", "anonymous"),
                    role=current_user.get("role", "researcher"),
                    prompt=query_req.question,
                    sql_query=res["query"],
                    latency_ms=elapsed_ms,
                    dataset=masked_parsed_data
                )
            except Exception as audit_err:
                print(f"Audit logging failed: {audit_err}")

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
            "rag_savings_pct": res.get("rag_savings_pct", 0.0),
            "retrieved_few_shots": res.get("retrieved_few_shots", []),
            "scan_breakdown": res.get("scan_breakdown", []),
            "optimizer_advisories": res.get("optimizer_advisories", []),
            "raw_plan": res.get("raw_plan", ""),
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
            "retrieved_tables": [],
            "rag_savings_pct": 0.0,
            "retrieved_few_shots": []
        }


@app.get("/api/history/{thread_id}")
def get_thread_history(thread_id: str, current_user: dict = Depends(get_current_user)):
    """Retrieves and parses the conversational message history for a given session thread ID."""
    if not agent:
        raise HTTPException(status_code=500, detail="Database agent is not initialized.")
    try:
        from langchain_core.messages import AIMessage, HumanMessage
        
        config = {"configurable": {"thread_id": thread_id}}
        state = agent.agent.get_state(config)
        messages = state.values.get("messages", [])
        
        # Group messages into sequential conversation turns
        turns = []
        current_turn = None
        
        for msg in messages:
            if isinstance(msg, HumanMessage):
                if current_turn:
                    turns.append(current_turn)
                current_turn = {
                    "question": msg.content,
                    "ai_messages": []
                }
            elif isinstance(msg, AIMessage) and current_turn is not None:
                current_turn["ai_messages"].append(msg.content)
                
        if current_turn:
            turns.append(current_turn)
            
        resolved_turns = []
        for turn in turns:
            question = turn["question"]
            ai_msgs = turn["ai_messages"]
            
            sql = ""
            result = ""
            summary = ""
            
            non_sql_non_schema = []
            for content in ai_msgs:
                if "SELECT" in content.upper() and not sql:
                    sql = content
                elif "CREATE TABLE" in content:
                    pass
                else:
                    non_sql_non_schema.append(content)
                    
            if len(non_sql_non_schema) == 1:
                summary = non_sql_non_schema[0]
            elif len(non_sql_non_schema) >= 2:
                summary = non_sql_non_schema[-1]
                result = non_sql_non_schema[0]
                
            # Apply dynamic masking on historical raw results if applicable
            masked_result = result
            if result and sql:
                try:
                    # Apply ABAC/PII policy masking just like E2E run
                    parsed_result = ast.literal_eval(result)
                    masked_parsed = apply_policy_masking(parsed_result, sql, current_user, agent)
                    masked_result = masked_parsed
                except Exception:
                    try:
                        import json
                        parsed_result = json.loads(result)
                        masked_parsed = apply_policy_masking(parsed_result, sql, current_user, agent)
                        masked_result = masked_parsed
                    except Exception:
                        pass
                
            resolved_turns.append({
                "question": question,
                "query": sql,
                "result": masked_result,
                "summary": summary
            })
            
        return {"success": True, "history": resolved_turns}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
