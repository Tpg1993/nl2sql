import os
import time
import re
import sqlite3
import sqlglot
import sqlglot.expressions as exp
from typing import Any, Dict, List, Optional
from sqlalchemy import create_engine
from langchain_community.utilities import SQLDatabase
from langchain_openai import ChatOpenAI
from langgraph.graph import MessagesState, StateGraph, START, END
from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langchain_core.messages import AIMessage, HumanMessage
from .few_shot_library import FewShotLibrary

# Load environment variables from .env file if available
try:
    from dotenv import load_dotenv
    # Look for .env in the same directory as this file
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass

TABLE_LOCATIONS = {
    "patients": "local",
    "vitals": "local",
    "allergies": "local",
    "diagnoses": "local",
    "medications": "local",
    "departments": "remote",
    "providers": "remote",
    "encounters": "remote",
    "lab_orders": "remote",
    "lab_results": "remote"
}

TABLE_DB_MAPPINGS = {
    "patients": "db_local_ehr",
    "vitals": "db_local_ehr",
    "allergies": "db_local_ehr",
    "diagnoses": "db_local_ehr",
    "medications": "db_local_ehr",
    "billing": "db_local_billing",
    "claims": "db_local_billing",
    "departments": "db_remote_warehouse",
    "providers": "db_remote_warehouse",
    "encounters": "db_remote_warehouse",
    "lab_orders": "db_remote_warehouse",
    "lab_results": "db_remote_warehouse"
}


def get_expr_key(expr):
    if expr.alias:
        return expr.alias.lower()
    if isinstance(expr, exp.Column):
        return expr.name.lower()
    col = expr.find(exp.Column)
    if col:
        return col.name.lower()
    return expr.sql().lower()


class AgentState(MessagesState):
    """Custom LangGraph state incorporating message history, retry count, and section latencies."""
    retries: int
    latencies: Dict[str, float]
    is_expert_matched: bool
    estimated_cost: float
    lineage: Dict[str, Any]
    pii_params: Dict[str, str]
    retrieved_tables: List[str]
    rag_savings_pct: float
    user_context: Dict[str, Any]
    retrieved_few_shots: List[Dict[str, Any]]
    scan_breakdown: List[Dict[str, Any]]
    optimizer_advisories: List[Dict[str, Any]]
    raw_plan: str


class EHRQueryAgent:
    """An agent that translates natural language queries to SQL, executes them 
    against a database, handles errors with a self-healing retry loop, and 
    summarizes the output into a plain English conversational response.
    """

    def __init__(self, db_uri: str = None) -> None:
        # Resolve DB path dynamically if not provided
        if db_uri is None:
            databricks_host = os.environ.get("DATABRICKS_HOST")
            databricks_token = os.environ.get("DATABRICKS_TOKEN")
            databricks_http_path = os.environ.get("DATABRICKS_HTTP_PATH")
            databricks_catalog = os.environ.get("DATABRICKS_CATALOG", "main")
            databricks_schema = os.environ.get("DATABRICKS_SCHEMA", "default")
            
            if databricks_host and databricks_token and databricks_http_path:
                print("\n[DB Router] DATABRICKS credentials discovered. Booting Databricks connection...")
                db_uri = f"databricks://token:{databricks_token}@{databricks_host}?http_path={databricks_http_path}&catalog={databricks_catalog}&schema={databricks_schema}"
            else:
                print("\n[DB Router] No Databricks credentials found. Cascading to local SQLite...")
                BASE_DIR = os.path.dirname(os.path.abspath(__file__))
                db_path = os.path.join(BASE_DIR, "ehr_data.db")
                db_uri = f"sqlite:///{db_path}"

        # Initialize connection and db utility
        if db_uri.startswith("sqlite://"):
            if db_uri.startswith("sqlite:///"):
                db_path = db_uri[10:]
            else:
                db_path = db_uri[9:]
            db_path = os.path.abspath(db_path)
            creator = lambda: sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            self.engine = create_engine("sqlite://", creator=creator)
        else:
            self.engine = create_engine(db_uri)
            
        self.db = SQLDatabase(self.engine, sample_rows_in_table_info=3)

        # Initialize multi-database connection pool
        self.db_engines = {}
        BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        
        # 1. Local EHR Database connection
        ehr_db_path = os.environ.get("SQLITE_EHR_DB_PATH")
        if not ehr_db_path:
            ehr_db_path = os.path.join(BASE_DIR, "ehr_data.db")
        ehr_db_path = os.path.abspath(ehr_db_path)
        ehr_creator = lambda: sqlite3.connect(f"file:{ehr_db_path}?mode=ro", uri=True)
        self.db_engines["db_local_ehr"] = create_engine("sqlite://", creator=ehr_creator)
        
        # 2. Local Billing Database connection
        billing_db_path = os.environ.get("SQLITE_BILLING_DB_PATH")
        if not billing_db_path:
            billing_db_path = ehr_db_path  # Fallback to EHR database
        billing_db_path = os.path.abspath(billing_db_path)
        billing_creator = lambda: sqlite3.connect(f"file:{billing_db_path}?mode=ro", uri=True)
        self.db_engines["db_local_billing"] = create_engine("sqlite://", creator=billing_creator)
        
        # 3. Remote Warehouse connection (using self.engine as the default remote engine)
        self.db_engines["db_remote_warehouse"] = self.engine

        
        print(f"Database Dialect: {self.db.dialect}")
        print(f"Available Tables: {self.db.get_usable_table_names()}")

        # Set up LLM with routing logic
        self.llm = self._init_llm()

        # Bind toolkit and dynamically locate required tools by name
        self.toolkit = SQLDatabaseToolkit(db=self.db, llm=self.llm)
        self.tools = self.toolkit.get_tools()
        
        try:
            self.list_tables_tool = next(t for t in self.tools if t.name == "sql_db_list_tables")
            self.get_schema_tool = next(t for t in self.tools if t.name == "sql_db_schema")
            self.run_query_tool = next(t for t in self.tools if t.name == "sql_db_query")
        except StopIteration as e:
            raise RuntimeError(
                "Required database tools (list tables, get schema, run query) were not found in the toolkit."
            ) from e

        # Initialize semantic layer, planner, override cache store, and prompt library
        from .semantic_layer import SemanticLayer
        from .planner import CostPlanner
        from .expert_overrides import ExpertOverrideStore
        from .prompts import PromptLibrary
        from .metadata_rag import MetadataRAG
        from .few_shot_library import FewShotLibrary
        self.semantic_layer = SemanticLayer()
        self.cost_planner = CostPlanner(self.engine)
        self.override_store = ExpertOverrideStore()
        self.prompt_library = PromptLibrary()
        self.metadata_rag = MetadataRAG(self.engine)
        self.few_shot_library = FewShotLibrary(self.semantic_layer.config_path)

        # Pre-calculate full DDL length for RAG savings metrics
        try:
            all_tables = self.db.get_usable_table_names()
            all_tables_str = ", ".join(all_tables)
            self.full_schema_ddl_len = len(self.get_schema_tool.invoke(all_tables_str))
        except Exception as e:
            print(f"[EHRQueryAgent] Warning: failed to pre-calculate full schema length: {e}")
            self.full_schema_ddl_len = 1000

        # Build and compile LangGraph state machine
        self.agent = self._compile_graph()

    def sanitize_and_extract_pii(self, question: str) -> tuple:
        """Redacts phone numbers, emails, and SSNs from the question, returning a tuple
        of (sanitized_question, parameter_map).
        """
        sanitized = question
        pii_params = {}
        param_counter = 0
        
        # 1. Email matching (ensuring we don't consume trailing punctuation like sentence periods)
        emails = re.findall(r"\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]*[a-zA-Z0-9-]\b", sanitized)
        for email in list(set(emails)):
            placeholder = f"PII_PARAM_{param_counter}"
            pii_params[placeholder] = email
            sanitized = sanitized.replace(email, placeholder)
            param_counter += 1
            
        # 2. Phone number matching (covering 7-digit local and 10-digit national/international formats)
        phones = re.findall(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?(?:\d{3}[-.\s]?)?\d{4}\b", sanitized)
        for phone in list(set(phones)):
            placeholder = f"PII_PARAM_{param_counter}"
            pii_params[placeholder] = phone
            sanitized = sanitized.replace(phone, placeholder)
            param_counter += 1
            
        # 3. SSN matching
        ssns = re.findall(r"\b\d{3}-\d{2}-\d{4}\b", sanitized)
        for ssn in list(set(ssns)):
            placeholder = f"PII_PARAM_{param_counter}"
            pii_params[placeholder] = ssn
            sanitized = sanitized.replace(ssn, placeholder)
            param_counter += 1
            
        return sanitized, pii_params

    def _init_llm(self) -> Any:
        """Initializes the LLM, defaulting to Sarvam AI if API key is present,
        otherwise cascading to OpenAI backup layer. Falls back to MockEHRLLM under testing
        or when keys are missing.
        """
        sarvam_key = os.environ.get("SARVAM_API_KEY")
        openai_key = os.environ.get("OPENAI_API_KEY")
        is_testing = os.environ.get("TESTING", "false").lower() == "true"
        
        if sarvam_key and not is_testing:
            print("\n[LLM Router] SARVAM_API_KEY discovered. Booting Sarvam AI as Primary...")
            return ChatOpenAI(
                model="sarvam-105b",
                openai_api_key=sarvam_key,
                openai_api_base="https://api.sarvam.ai/v1",
                temperature=0
            )
            
        if openai_key and not is_testing:
            print("\n[LLM Router] OpenAI API Key found. Booting ChatOpenAI...")
            return ChatOpenAI(
                model="gpt-4o-mini",
                openai_api_key=openai_key,
                temperature=0
            )
            
        # No keys found or under testing: fallback to local MockEHRLLM to allow offline validation
        print("\n[LLM Router] Warning: Running under test mode or no API keys found. Booting MockEHRLLM fallback layer...")
        from langchain_core.language_models.chat_models import BaseChatModel
        from langchain_core.messages import BaseMessage, AIMessage
        from langchain_core.outputs import ChatResult, ChatGeneration
        
        class MockEHRLLM(BaseChatModel):
            model_name: str = "mock-ehr-llm"
            
            @property
            def _llm_type(self) -> str:
                return "mock-ehr-llm"
                
            def _generate(
                self,
                messages: List[BaseMessage],
                stop: Optional[List[str]] = None,
                run_manager: Optional[Any] = None,
                **kwargs: Any
            ) -> ChatResult:
                # Compile prompt from messages
                prompt_text = ""
                for m in messages:
                    prompt_text += getattr(m, "content", "") + "\n"
                prompt_lower = prompt_text.lower()
                
                # Try to extract the user's actual query from the formatted prompt templates
                user_q = ""
                match = re.search(r"User Query Request:\s*(.*)", prompt_text, re.IGNORECASE)
                if not match:
                    match = re.search(r"User Question:\s*(.*)", prompt_text, re.IGNORECASE)
                if not match:
                    match = re.search(r"Original Question:\s*(.*)", prompt_text, re.IGNORECASE)
                    
                if match:
                    user_q = match.group(1).split("\n")[0].strip()
                else:
                    # Fallback to standard human message extraction
                    human_messages = [m.content for m in messages if getattr(m, "type", "") == "human" or m.__class__.__name__ == "HumanMessage"]
                    user_q = "\n".join(human_messages) if human_messages else (messages[-1].content if messages else "")
                
                user_q_lower = user_q.lower()
                
                # Determine responses based on question intents using the user's specific query
                sql_query = "SELECT COUNT(*) FROM patients"
                summary = "There are 1000 patients in total."
                
                if "select" in user_q_lower:
                    sql_query = user_q
                    summary = "Here is the federated query result."
                elif "provider" in user_q_lower:
                    sql_query = "SELECT d.department_name, COUNT(p.provider_id) AS provider_count FROM providers p JOIN departments d ON p.department_id = d.department_id GROUP BY d.department_name ORDER BY provider_count DESC LIMIT 5;"
                    summary = "The top 5 departments by provider count are Emergency (9 providers), Neurology (8 providers), Nephrology (7 providers), Pulmonology (7 providers), and General Medicine (7 providers)."
                elif "allerg" in user_q_lower:
                    sql_query = "SELECT p.patient_id, p.full_name FROM patients p JOIN allergies a ON p.patient_id = a.patient_id WHERE a.is_active = 1;"
                    summary = "The query identified 68 unique patients with active allergies."
                elif "encounter" in user_q_lower:
                    sql_query = "SELECT * FROM encounters;"
                    summary = "Show all records from encounters."
                elif "patient" in user_q_lower:
                    if "detail" in user_q_lower:
                        sql_query = "SELECT patient_id, mrn, full_name, gender, dob, phone, email, city, state, blood_group, marital_status FROM patients;"
                        summary = "Here are the patient details including PII fields."
                    else:
                        sql_query = "SELECT COUNT(*) FROM patients;"
                        summary = "There are 1000 patients in total."
                        
                is_summarize = "summarize" in prompt_lower or "result" in prompt_lower or "clinical summary" in prompt_lower or "conversational results summary" in prompt_lower
                
                content = summary if is_summarize else sql_query
                ai_msg = AIMessage(content=content)
                ai_msg.usage_metadata = {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "total_tokens": 150
                }
                return ChatResult(generations=[ChatGeneration(message=ai_msg)])
                
        return MockEHRLLM()

    def retrieve_schema_node(self, state: AgentState) -> Dict[str, Any]:
        """Node 1 & 2 (Combined): Retrieve relevant tables using Metadata RAG and fetch their schema specifications."""
        print("\n[Node RAG] Performing semantic metadata retrieval...")
        start_time = time.perf_counter()
        user_question = state["messages"][0].content
        
        # Call Metadata RAG to get relevant tables
        retrieved_tables = self.metadata_rag.retrieve_tables(user_question, top_k=3)
        print(f"-> RAG Retrieved Tables: {retrieved_tables}")
        
        # Build schema context for only these tables
        table_names_str = ", ".join(retrieved_tables)
        schema_result = self.get_schema_tool.invoke(table_names_str)
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        
        latencies = state.get("latencies", {}).copy()
        latencies["schema"] = latencies.get("schema", 0.0) + elapsed_ms
        
        # Calculate schema prompt character savings percentage
        retrieved_len = len(schema_result) if schema_result else 0
        full_len = getattr(self, "full_schema_ddl_len", 1000)
        rag_savings_pct = round((1 - retrieved_len / full_len) * 100, 2) if full_len > 0 else 0.0
        rag_savings_pct = max(0.0, min(100.0, rag_savings_pct))
        print(f"-> RAG Schema Prompt Savings: {rag_savings_pct}% ({retrieved_len} / {full_len} chars)")
        
        return {
            "messages": [AIMessage(content=schema_result)],
            "latencies": latencies,
            "retrieved_tables": retrieved_tables,
            "rag_savings_pct": rag_savings_pct
        }

    def generate_query_node(self, state: AgentState) -> Dict[str, Any]:
        """Node 3: Generate SQL query based on user question, DB schema, and semantic layer, taking error details on retry."""
        print("\n[Node 3] Building execution query context...")
        start_time = time.perf_counter()
        user_question = state["messages"][0].content
        
        # 1. Pre-execution expert override check (RLHF)
        user_context = state.get("user_context") or {}
        username = user_context.get("username")
        override_sql = self.override_store.get_override(user_question, username=username)
        if override_sql:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            latencies = state.get("latencies", {}).copy()
            latencies["generation"] = latencies.get("generation", 0.0) + elapsed_ms
            return {
                "messages": [AIMessage(content=override_sql)],
                "latencies": latencies,
                "is_expert_matched": True,
                "retrieved_few_shots": []
            }

        # Locate schema description in message history
        db_schema = ""
        for msg in reversed(state["messages"]):
            if isinstance(msg, AIMessage) and "CREATE TABLE" in msg.content:
                db_schema = msg.content
                break
        if not db_schema:
            db_schema = state["messages"][1].content if len(state["messages"]) > 1 else ""

        # Identify previous execution errors for self-healing prompt injections
        retries = state.get("retries", 0)
        previous_error = ""
        if retries > 0:
            last_msg = state["messages"][-1].content
            if last_msg.startswith("Error executing query:"):
                previous_error = last_msg

        # Retrieve relevant few-shot examples from semantic layer configuration
        retrieved_few_shots = self.few_shot_library.retrieve_few_shots(user_question, top_k=2)
        print(f"-> Few-Shot RAG Matches: {retrieved_few_shots}")
        
        few_shot_context = ""
        if retrieved_few_shots:
            few_shot_context = "Reference Examples of Similar Queries:\n"
            for idx, ex in enumerate(retrieved_few_shots):
                few_shot_context += f"Example {idx + 1}:\n"
                few_shot_context += f"Question: {ex.get('question')}\n"
                few_shot_context += f"SQL: {ex.get('sql')}\n\n"

        prompt = self.prompt_library.format_sql_generation(
            user_question=user_question,
            semantic_context=self.semantic_layer.get_context_prompt(),
            db_schema=db_schema,
            previous_error=previous_error,
            few_shot_context=few_shot_context
        )
        
        result = self.llm.invoke(prompt)
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        
        latencies = state.get("latencies", {}).copy()
        latencies["generation"] = latencies.get("generation", 0.0) + elapsed_ms
        
        print(f"-> Active Model Prompt Output: {result.content}")
        return {
            "messages": [result],
            "latencies": latencies,
            "is_expert_matched": False,
            "retrieved_few_shots": retrieved_few_shots
        }

    @staticmethod
    def strip_sql_literals(sql: str) -> str:
        """Removes single and double quoted string literals from SQL query to avoid false positives on keywords."""
        sql_no_singles = re.sub(r"'[^']*(?:''[^']*)*'", " ", sql)
        sql_no_doubles = re.sub(r'"[^"]*(?:""[^"]*)*"', " ", sql_no_singles)
        return sql_no_doubles

    def audit_sql_query(self, sql_query: str) -> None:
        """Audits the generated SQL query for dangerous write/schema alteration operations using AST parsing."""
        if not sql_query or not sql_query.strip():
            return
            
        try:
            # Determine read dialect based on connection engine if initialized
            dialect = "sqlite"
            if hasattr(self, "engine") and self.engine and self.engine.url.drivername.startswith("databricks"):
                dialect = "databricks"
                
            parsed_expressions = sqlglot.parse(sql_query, read=dialect)
        except Exception:
            try:
                # Fallback to generic SQL parsing if dialect-specific strictly fails
                parsed_expressions = sqlglot.parse(sql_query)
            except Exception as parse_err:
                raise ValueError(f"Security violation: Failed to parse SQL query structure. Details: {parse_err}")
                
        # Define structural expressions that represent modification statements
        MUTATION_EXPRESSIONS = (
            exp.Insert,
            exp.Update,
            exp.Delete,
            exp.Drop,
            exp.AlterTable,
            exp.AlterColumn,
            exp.Create,
            exp.TruncateTable,
            exp.Command
        )
        
        for expr in parsed_expressions:
            if not expr:
                continue
            for node in expr.walk():
                if isinstance(node, MUTATION_EXPRESSIONS):
                    node_name = node.__class__.__name__.upper()
                    raise ValueError(f"Security violation: Disallowed SQL command structure '{node_name}' detected.")
                
                # Check for generic SQL Commands that execute administrative scripts (e.g. PRAGMA, VACUUM)
                if isinstance(node, exp.Command):
                    cmd_name = str(node.this).upper()
                    if cmd_name in ["PRAGMA", "VACUUM", "REPLACE"]:
                        raise ValueError(f"Security violation: Disallowed SQL command keyword '{cmd_name}' detected.")

    def execute_query_node(self, state: AgentState) -> Dict[str, Any]:
        """Node 4: Execute SQL query on the database, with exception tracking for retry router."""
        print("\n[Node 4] Injecting context payload into DB engine...")
        start_time = time.perf_counter()
        sql_query = str(state["messages"][-1].content).strip()
        
        # Safe extraction if state messages are out of order
        target_msg = state["messages"][-1]
        if sql_query.startswith("Error executing query:"):
            for msg in reversed(state["messages"]):
                content = str(msg.content).strip()
                if not content.startswith("Error executing query:") and "SELECT" in content.upper():
                    sql_query = content
                    target_msg = msg
                    break
        
        # Restore PII parameter values in the SQL query text before safety checks and execution
        pii_params = state.get("pii_params", {})
        for placeholder, original_val in pii_params.items():
            sql_query = sql_query.replace(placeholder, original_val)
        target_msg.content = sql_query

        try:
            if not sql_query or not sql_query.strip():
                raise ValueError("Security violation: Safe SQL query could not be compiled for this request.")

            # Security Guardrail Check
            self.audit_sql_query(sql_query)

            # Cost Planner Check
            is_safe, cost_reason, cost_metrics = self.cost_planner.analyze_query(sql_query)
            
            # Check for HighPerformanceQueryGroup cost check bypass
            user_context = state.get("user_context") or {}
            user_role = user_context.get("role")
            
            high_perf_groups = self.semantic_layer.security_policies.get("high_performance_groups", {})
            bypass_roles = high_perf_groups.get("HighPerformanceQueryGroup", [])
            
            is_bypassed = False
            if user_role == "admin" or (user_role and user_role in bypass_roles):
                is_bypassed = True
                print(f"[CostPlanner] Bypassing cost safety check for user role '{user_role}'.")
                
            if not is_safe and not is_bypassed:
                raise ValueError(f"Cost violation: {cost_reason}")

            # Enforce default LIMIT 100 if no LIMIT is specified in SELECT query
            if "SELECT" in sql_query.upper() and not re.search(r'\blimit\b', sql_query, re.IGNORECASE):
                has_semicolon = False
                query_stripped = sql_query.rstrip()
                if query_stripped.endswith(';'):
                    query_stripped = query_stripped[:-1].rstrip()
                    has_semicolon = True
                sql_query = f"{query_stripped} LIMIT 100"
                if has_semicolon:
                    sql_query += ";"
                print(f"-> Limit enforced. Wrapped query: {sql_query}")
                
                # Update the message in-place in the graph state so the modified query is returned and displayed
                target_msg.content = sql_query

            # Execute query (Federated or Standard)
            if self.is_federated_query(sql_query):
                result = self.execute_federated_query(sql_query)
            else:
                result = self.run_query_tool.invoke(sql_query)

            if isinstance(result, str) and (result.startswith("Error") or "Error:" in result):
                raise ValueError(result)
                
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            
            latencies = state.get("latencies", {}).copy()
            latencies["execution"] = latencies.get("execution", 0.0) + elapsed_ms
            
            # Extract query lineage details
            lineage_data = self.extract_lineage(sql_query)
            
            # Map cost details
            total_scanned = cost_metrics.get("total_estimated_rows_scanned", 1)
            estimated_cost = total_scanned * 0.1 if self.cost_planner.dialect_name == "sqlite" else 10.0
            
            return {
                "messages": [AIMessage(content=result)],
                "latencies": latencies,
                "lineage": lineage_data,
                "estimated_cost": estimated_cost,
                "scan_breakdown": cost_metrics.get("scan_breakdown", []),
                "optimizer_advisories": cost_metrics.get("optimizer_advisories", []),
                "raw_plan": cost_metrics.get("raw_plan", "")
            }
        except Exception as e:
            err_str = str(e)
            err_str_lower = err_str.lower()
            is_security = (
                "security violation" in err_str_lower or
                "readonly database" in err_str_lower or
                "read-only" in err_str_lower or
                "read only" in err_str_lower or
                "write violation" in err_str_lower or
                "permission denied" in err_str_lower or
                "does not have privilege" in err_str_lower or
                "unauthorized" in err_str_lower
            )
            is_cost = "cost violation" in err_str_lower
            
            if is_security and not err_str.startswith("Security violation"):
                result = f"Error executing query: Security violation: {err_str}"
            elif is_cost:
                result = f"Error executing query: Cost violation: {err_str}"
            else:
                result = f"Error executing query: {err_str}"
                
            print(f"-> Query Execution Failed: {result}")
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            
            latencies = state.get("latencies", {}).copy()
            latencies["execution"] = latencies.get("execution", 0.0) + elapsed_ms
            current_retries = state.get("retries", 0)
            
            # If it's a security or cost violation, do NOT increment retries
            next_retries = current_retries if (is_security or is_cost) else current_retries + 1
            
            ret_payload = {
                "messages": [AIMessage(content=result)],
                "retries": next_retries,
                "latencies": latencies
            }
            # Still propagate parser metrics to the UI even on failure
            if 'cost_metrics' in locals():
                total_scanned = cost_metrics.get("total_estimated_rows_scanned", 1)
                ret_payload["estimated_cost"] = total_scanned * 0.1 if self.cost_planner.dialect_name == "sqlite" else 10.0
                ret_payload["scan_breakdown"] = cost_metrics.get("scan_breakdown", [])
                ret_payload["optimizer_advisories"] = cost_metrics.get("optimizer_advisories", [])
                ret_payload["raw_plan"] = cost_metrics.get("raw_plan", "")
            return ret_payload

    def should_retry(self, state: AgentState) -> str:
        """Conditional edge router checking retry limit on query execution errors."""
        last_msg = state["messages"][-1].content
        retries = state.get("retries", 0)
        
        if "Security violation" in last_msg or "Cost violation" in last_msg:
            print("\n[Router] Security or Cost violation detected. Routing to summarize_results immediately...")
            return "summarize_results"
            
        if last_msg.startswith("Error executing query:") and retries < 2:
            print(f"\n[Router] Query failed. Retry count: {retries}/2. Routing back to generate_query...")
            return "generate_query"
            
        print("\n[Router] Query succeeded or retry limit reached. Routing to summarize_results...")
        return "summarize_results"

    def summarize_results_node(self, state: AgentState) -> Dict[str, Any]:
        """Node 5: Generates a natural language clinical summary of database results."""
        print("\n[Node 5] Generating conversational results summary...")
        start_time = time.perf_counter()
        user_question = state["messages"][0].content
        
        # Traverse history to identify query outputs and query string
        db_result = ""
        sql_query = ""
        for msg in reversed(state["messages"]):
            content = msg.content
            if content.startswith("Error executing query:"):
                db_result = content
                break
            elif not db_result:
                db_result = content
            
            if "SELECT" in content.upper() and not sql_query:
                sql_query = content

        # Check for security or cost violation first to bypass LLM and return static response
        if "Security violation" in db_result:
            summary_content = "This query was blocked because it violates database security guardrails (read-only enforcement). Only safe SELECT queries are permitted."
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            
            latencies = state.get("latencies", {}).copy()
            latencies["summarization"] = latencies.get("summarization", 0.0) + elapsed_ms
            return {"messages": [AIMessage(content=summary_content)], "latencies": latencies}

        if "Cost violation" in db_result:
            reason_match = re.search(r"Cost violation: (.*)", db_result)
            reason_text = reason_match.group(1) if reason_match else db_result
            summary_content = f"This query was blocked by the resource cost planner. Reason: {reason_text}"
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            
            latencies = state.get("latencies", {}).copy()
            latencies["summarization"] = latencies.get("summarization", 0.0) + elapsed_ms
            return {"messages": [AIMessage(content=summary_content)], "latencies": latencies}

        is_error = db_result.startswith("Error executing query:")

        # Apply compliance masking/redaction to database results prior to LLM summarization
        user_context = state.get("user_context")
        if user_context and not is_error and db_result and sql_query:
            import ast
            try:
                parsed_db = ast.literal_eval(db_result)
            except Exception:
                parsed_db = db_result
            if isinstance(parsed_db, list):
                from .app import apply_policy_masking
                masked_db = apply_policy_masking(parsed_db, sql_query, user_context, self)
                db_result = str(masked_db)

        # Truncate database result if it exceeds a safe size (e.g. 5000 characters)
        if len(db_result) > 5000:
            print(f"-> Truncating large database result from {len(db_result)} to 5000 characters to prevent API payload limits.")
            db_result = db_result[:5000] + "\n... [Truncated for LLM payload size limits]"

        prompt = self.prompt_library.format_summarization(
            user_question=user_question,
            db_result=db_result,
            error_context=is_error
        )
            
        result = self.llm.invoke(prompt)
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        
        latencies = state.get("latencies", {}).copy()
        latencies["summarization"] = latencies.get("summarization", 0.0) + elapsed_ms
        
        print(f"-> Conversational Summary: {result.content}")
        return {"messages": [result], "latencies": latencies}

    def is_federated_query(self, sql_query: str) -> bool:
        """Determines if a query requires federated join across different databases."""
        if not sql_query or not sql_query.strip():
            return False
        try:
            parsed = sqlglot.parse_one(sql_query)
            tables = [t.name.lower() for t in parsed.find_all(exp.Table)]
            
            involved_dbs = set()
            for t in tables:
                db_name = TABLE_DB_MAPPINGS.get(t)
                if db_name:
                    involved_dbs.add(db_name)
                    
            return len(involved_dbs) > 1
        except Exception:
            # Fallback simple search
            query_lower = sql_query.lower()
            involved_dbs = set()
            for table, db_name in TABLE_DB_MAPPINGS.items():
                if table in query_lower:
                    involved_dbs.add(db_name)
            return len(involved_dbs) > 1

    def decompose_query(self, sql: str):
        parsed = sqlglot.parse_one(sql)
        
        # 1. Map aliases to table names
        table_aliases = {}
        for table in parsed.find_all(exp.Table):
            alias = table.alias
            table_name = table.name.lower()
            table_aliases[alias or table_name] = table_name

        # 2. Track database name for aliases
        alias_dbs = {}
        dbs_aliases = {}
        for alias, tbl in table_aliases.items():
            db_name = TABLE_DB_MAPPINGS.get(tbl, "db_local_ehr")
            alias_dbs[alias] = db_name
            dbs_aliases.setdefault(db_name, set()).add(alias)

        # 3. Find cross-boundary join conditions
        join_keys_by_db = {db: {} for db in TABLE_DB_MAPPINGS.values()}
        join_conditions = []
        
        for join in parsed.find_all(exp.Join):
            on_expr = join.args.get("on")
            if on_expr:
                for eq in on_expr.find_all(exp.EQ):
                    left = eq.left
                    right = eq.right
                    if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                        left_alias = left.table or left.name
                        right_alias = right.table or right.name
                        left_tbl = table_aliases.get(left_alias, left_alias)
                        right_tbl = table_aliases.get(right_alias, right_alias)
                        
                        left_db = alias_dbs.get(left_alias, "db_local_ehr")
                        right_db = alias_dbs.get(right_alias, "db_local_ehr")
                        
                        if left_db != right_db:
                            if left_db in ("db_local_ehr", "db_local_billing"):
                                join_keys_by_db[left_db].setdefault(left_alias, set()).add(left.name.lower())
                                join_keys_by_db[right_db].setdefault(right_alias, set()).add(right.name.lower())
                                join_conditions.append((left_alias, left.name.lower(), right_alias, right.name.lower()))
                            else:
                                join_keys_by_db[right_db].setdefault(left_alias, set()).add(left.name.lower())
                                join_keys_by_db[left_db].setdefault(right_alias, set()).add(right.name.lower())
                                join_conditions.append((right_alias, right.name.lower(), left_alias, left.name.lower()))
                                
        # 4. Group select expressions by database
        selects_by_db = {db: [] for db in TABLE_DB_MAPPINGS.values()}
        LOCAL_COLUMNS = {
            "patient_id", "mrn", "full_name", "gender", "dob", "phone", "email", "city", "state", "blood_group", "marital_status",
            "vital_id", "measurement_date", "systolic", "diastolic", "pulse", "temperature", "respiratory_rate",
            "allergy_id", "allergy_name", "reaction", "severity", "is_active",
            "diagnosis_id", "diagnosis_code", "diagnosis_name", "diagnosed_date",
            "medication_id", "medication_name", "dosage", "frequency", "start_date", "end_date"
        }
        for expr in parsed.expressions:
            referenced_aliases = {c.table for c in expr.find_all(exp.Column) if c.table}
            if referenced_aliases:
                for a in referenced_aliases:
                    db = alias_dbs.get(a, "db_local_ehr")
                    selects_by_db.setdefault(db, []).append(expr)
            else:
                cols = [c.name.lower() for c in expr.find_all(exp.Column)]
                for col in cols:
                    db = "db_local_ehr" if col in LOCAL_COLUMNS else "db_remote_warehouse"
                    selects_by_db.setdefault(db, []).append(expr)
                    
        # 5. Add join keys to selects
        for db_name, join_keys in join_keys_by_db.items():
            for alias, keys in join_keys.items():
                for key in keys:
                    col_expr = exp.Column(this=exp.to_identifier(key), table=exp.to_identifier(alias))
                    selects = selects_by_db.setdefault(db_name, [])
                    if not any(s.sql().lower() == col_expr.sql().lower() or (s.alias.lower() == key if s.alias else False) for s in selects):
                        selects.append(col_expr)

        # Helper to build database sub-query
        def build_db_query(db_selects, db_aliases):
            if not db_selects or not db_aliases:
                return ""
            from_node = parsed.args.get("from")
            main_table_node = None
            if from_node:
                main_table_node = from_node.this
                main_alias = main_table_node.alias or main_table_node.name.lower()
                if main_alias not in db_aliases:
                    main_table_node = None
            
            if not main_table_node:
                for join in parsed.find_all(exp.Join):
                    join_table = join.this
                    j_alias = join_table.alias or join_table.name.lower()
                    if j_alias in db_aliases:
                        main_table_node = join_table
                        break
            
            if not main_table_node:
                return ""
            
            alias_str = f" AS {main_table_node.alias}" if main_table_node.alias else ""
            query_str = "SELECT " + ", ".join(s.sql() for s in db_selects)
            query_str += f" FROM {main_table_node.name}{alias_str}"
            
            for join in parsed.find_all(exp.Join):
                join_table = join.this
                j_alias = join_table.alias or join_table.name.lower()
                if j_alias in db_aliases and join_table != main_table_node:
                    query_str += f" {join.sql()}"
            
            where_node = parsed.args.get("where")
            side_filters = []
            if where_node:
                def get_cond_loc(cond):
                    cols = list(cond.find_all(exp.Column))
                    aliases = {c.table for c in cols if c.table}
                    is_s = any(a in db_aliases for a in aliases)
                    is_oth = any(a not in db_aliases for a in aliases)
                    if is_s and not is_oth:
                        return "side"
                    if is_oth and not is_s:
                        return "other"
                    return "both"

                def traverse_and(node):
                    if isinstance(node, exp.And):
                        return traverse_and(node.left) + traverse_and(node.right)
                    return [node]

                flat_conds = traverse_and(where_node.this)
                for cond in flat_conds:
                    if get_cond_loc(cond) == "side":
                        side_filters.append(cond)
            
            if side_filters:
                query_str += " WHERE " + " AND ".join(c.sql() for c in side_filters)
                
            return query_str

        # Generate sub-queries for all active databases
        queries_by_db = {}
        for db, db_aliases in dbs_aliases.items():
            queries_by_db[db] = build_db_query(selects_by_db.get(db, []), db_aliases)
            
        self.last_decomposed_queries = queries_by_db
        
        local_query = queries_by_db.get("db_local_ehr", "")
        remote_query = queries_by_db.get("db_remote_warehouse", "")
        
        return local_query, remote_query, join_conditions, parsed

    def execute_federated_query(self, sql_query: str) -> str:
        """Executes federated query by querying SQLite and Databricks separately, and joining outcomes in-memory."""
        print("-> Running Federated Join across multiple databases...")
        print("-> Running Federated Join across local SQLite and remote Databricks...")
        try:
            local_q, remote_q, joins, parsed = self.decompose_query(sql_query)
            print(f"Local query decomposed: {local_q}")
            print(f"Remote query decomposed: {remote_q}")
            
            # Map aliases to table names and databases
            table_aliases = {}
            for table in parsed.find_all(exp.Table):
                alias = table.alias
                table_name = table.name.lower()
                table_aliases[alias or table_name] = table_name
                
            def get_alias_db(alias):
                tbl = table_aliases.get(alias, alias)
                return TABLE_DB_MAPPINGS.get(tbl, "db_local_ehr")

            if not joins:
                raise ValueError("No cross-boundary join conditions found in the query.")
                
            local_alias, local_key, remote_alias, remote_key = joins[0]
            local_db = get_alias_db(local_alias)
            remote_db = get_alias_db(remote_alias)
            
            # Execute local query
            local_engine = self.db_engines.get(local_db)
            if not local_engine:
                raise ValueError(f"No database connection engine configured for: {local_db}")
                
            local_rows = []
            if local_q:
                from sqlalchemy import text
                with local_engine.connect() as conn:
                    res = conn.execute(text(local_q))
                    keys = [k.lower() for k in res.keys()]
                    for row in res:
                        local_rows.append({k: v for k, v in zip(keys, row)})
                        
            # Extract candidate key values
            key_values = set()
            for row in local_rows:
                val = row.get(local_key)
                if val is not None:
                    key_values.add(val)
                    
            # Short-circuit remote query execution if local side has no candidate matches
            if not key_values:
                print("-> Semi-Join Pushdown: No candidate key matches found in local DB. Short-circuiting remote execution.")
                return "[]"
                
            # Semi-Join Pushdown: dynamically inject keys as IN constraint if size is reasonable
            if len(key_values) <= 1000:
                print(f"-> Semi-Join Pushdown: Injecting {len(key_values)} candidate keys into remote sub-query.")
                try:
                    parsed_remote = sqlglot.parse_one(remote_q)
                    in_clause = exp.In(
                        this=exp.column(remote_key, table=remote_alias),
                        expressions=[exp.Literal.number(v) if isinstance(v, (int, float)) else exp.Literal.string(str(v)) for v in key_values]
                    )
                    parsed_remote = parsed_remote.where(in_clause)
                    remote_q_optimized = parsed_remote.sql()
                    print(f"-> Optimized remote query: {remote_q_optimized}")
                    remote_q = remote_q_optimized
                except Exception as pe:
                    print(f"-> Semi-Join Pushdown optimization failed: {pe}. Falling back to standard query.")
            else:
                print(f"-> Semi-Join Pushdown bypassed (large keys list: {len(key_values)}). Running full remote query.")
                
            # Execute remote query
            remote_engine = self.db_engines.get(remote_db)
            if not remote_engine:
                raise ValueError(f"No database connection engine configured for: {remote_db}")
                
            remote_rows = []
            if remote_q:
                from sqlalchemy import text
                with remote_engine.connect() as conn:
                    res = conn.execute(text(remote_q))
                    keys = [k.lower() for k in res.keys()]
                    for row in res:
                        remote_rows.append({k: v for k, v in zip(keys, row)})
                        
            # Perform client-side hash join
            local_lookup = {}
            for row in local_rows:
                key_val = row.get(local_key)
                if key_val is not None:
                    local_lookup.setdefault(key_val, []).append(row)
                    
            unified = []
            for r_row in remote_rows:
                r_key_val = r_row.get(remote_key)
                if r_key_val in local_lookup:
                    for l_row in local_lookup[r_key_val]:
                        merged = {}
                        for expr in parsed.expressions:
                            key = get_expr_key(expr)
                            if key in l_row:
                                merged[key] = l_row[key]
                            elif key in r_row:
                                merged[key] = r_row[key]
                        row_tuple = tuple(merged.get(get_expr_key(expr)) for expr in parsed.expressions)
                        unified.append(row_tuple)
                        
            # Apply ORDER BY sorting if present
            order_node = parsed.args.get("order")
            if order_node and order_node.expressions:
                first_expr = order_node.expressions[0]
                col_name = first_expr.this.name.lower() if hasattr(first_expr.this, "name") else str(first_expr.this).lower()
                desc = first_expr.args.get("desc", False)
                
                # Find index of this column in selects
                sort_col_idx = -1
                for idx, select_expr in enumerate(parsed.expressions):
                    if get_expr_key(select_expr) == col_name:
                        sort_col_idx = idx
                        break
                        
                if sort_col_idx != -1:
                    def get_sort_val(x):
                        val = x[sort_col_idx]
                        if val is None:
                            return "" if isinstance(val, str) else 0
                        return val
                    unified.sort(key=get_sort_val, reverse=desc)
                    
            # Apply LIMIT and OFFSET if present
            limit_node = parsed.args.get("limit")
            offset_node = parsed.args.get("offset")
            
            offset_val = 0
            if offset_node:
                offset_val = int(offset_node.expression.this)
                
            if limit_node:
                limit_val = int(limit_node.expression.this)
                unified = unified[offset_val : offset_val + limit_val]
            elif offset_val > 0:
                unified = unified[offset_val:]
                
            return str(unified)
            
        except Exception as e:
            print(f"Federated query failed: {e}")
            raise e

    def extract_lineage(self, sql_query: str) -> Dict[str, Any]:
        """Extracts tables, columns, joins, and filter clauses from SQL for lineage visualization."""
        query_clean = sql_query.replace("\n", " ").replace("\t", " ")
        
        # 1. Extract tables
        from_matches = re.findall(r"\bFROM\s+(\w+)", query_clean, re.IGNORECASE)
        join_matches = re.findall(r"\bJOIN\s+(\w+)", query_clean, re.IGNORECASE)
        tables = list(set(from_matches + join_matches))
        
        # 2. Extract joins
        joins = []
        join_clause_matches = re.findall(r"\bJOIN\s+(\w+)\s+ON\s+([^ ]+)\s*=\s*([^ ]+)", query_clean, re.IGNORECASE)
        for match in join_clause_matches:
            joins.append({
                "table": match[0],
                "condition": f"{match[1]} = {match[2]}"
            })
            
        # 3. Extract filters
        filters = []
        where_match = re.search(r"\bWHERE\s+(.*)", query_clean, re.IGNORECASE)
        if where_match:
            where_clause = where_match.group(1)
            filter_parts = re.split(r"\bAND\b|\bOR\b", where_clause, flags=re.IGNORECASE)
            for part in filter_parts:
                part_clean = part.split("LIMIT")[0].split("ORDER BY")[0].split("GROUP BY")[0].strip()
                if part_clean:
                    filters.append(part_clean)
                    
        # 4. Extract columns
        select_match = re.search(r"\bSELECT\s+(.*?)\s+FROM\b", query_clean, re.IGNORECASE)
        columns = []
        if select_match:
            cols_str = select_match.group(1)
            columns = [c.strip() for c in cols_str.split(",") if c.strip()]
            
        return {
            "tables": tables,
            "columns": columns,
            "joins": joins,
            "filters": filters
        }

    def _compile_graph(self) -> Any:
        """Sets up the state graph nodes, edges, and compiles it."""
        builder = StateGraph(AgentState)
        
        # Register nodes
        builder.add_node("retrieve_schema", self.retrieve_schema_node)
        builder.add_node("generate_query", self.generate_query_node)
        builder.add_node("execute_query", self.execute_query_node)
        builder.add_node("summarize_results", self.summarize_results_node)
        
        # Configure transitions
        builder.add_edge(START, "retrieve_schema")
        builder.add_edge("retrieve_schema", "generate_query")
        builder.add_edge("generate_query", "execute_query")
        
        # Set up retry conditional logic on execute query
        builder.add_conditional_edges(
            "execute_query",
            self.should_retry,
            {
                "generate_query": "generate_query",
                "summarize_results": "summarize_results"
            }
        )
        builder.add_edge("summarize_results", END)
        
        return builder.compile()

    def query(self, question: str, user_context: dict = None) -> str:
        """Runs the compiled graph workflow for a natural language question, sanitizing input PII."""
        gray_question, pii_params = self.sanitize_and_extract_pii(question)
        initial_state = {
            "messages": [HumanMessage(content=gray_question)],
            "retries": 0,
            "latencies": {
                "schema": 0.0,
                "generation": 0.0,
                "execution": 0.0,
                "summarization": 0.0
            },
            "pii_params": pii_params,
            "retrieved_tables": [],
            "rag_savings_pct": 0.0,
            "user_context": user_context,
            "retrieved_few_shots": []
        }
        output = self.agent.invoke(initial_state)
        return output["messages"][-1].content

    def query_detailed(self, question: str, user_context: dict = None) -> dict:
        """Runs the compiled graph workflow and returns generated SQL, DB results, conversational summary, and LLM token usage."""
        sanitized_question, pii_params = self.sanitize_and_extract_pii(question)
        initial_state = {
            "messages": [HumanMessage(content=sanitized_question)],
            "retries": 0,
            "latencies": {
                "schema": 0.0,
                "generation": 0.0,
                "execution": 0.0,
                "summarization": 0.0
            },
            "pii_params": pii_params,
            "retrieved_tables": [],
            "rag_savings_pct": 0.0,
            "user_context": user_context,
            "retrieved_few_shots": []
        }
        output = self.agent.invoke(initial_state)

        
        sql_query = ""
        db_result = ""
        conversational_summary = ""
        token_usage = None
        
        if len(output["messages"]) >= 1:
            conversational_summary = output["messages"][-1].content
            
        # Traverse messages backwards to separate SQL statement and DB execution outputs
        for msg in reversed(output["messages"][:-1]):
            content = msg.content
            if "SELECT" in content.upper() and not sql_query:
                sql_query = content
            elif not db_result:
                db_result = content
                
        # Aggregate token usage across all steps
        input_tokens = 0
        output_tokens = 0
        total_tokens = 0
        has_usage = False
        
        for msg in output["messages"]:
            if isinstance(msg, AIMessage):
                if hasattr(msg, "usage_metadata") and msg.usage_metadata:
                    input_tokens += msg.usage_metadata.get("input_tokens", 0)
                    output_tokens += msg.usage_metadata.get("output_tokens", 0)
                    total_tokens += msg.usage_metadata.get("total_tokens", 0)
                    has_usage = True
                elif "token_usage" in msg.response_metadata:
                    t_use = msg.response_metadata["token_usage"]
                    input_tokens += t_use.get("prompt_tokens", 0)
                    output_tokens += t_use.get("completion_tokens", 0)
                    total_tokens += t_use.get("total_tokens", 0)
                    has_usage = True
                    
        if has_usage:
            token_usage = {
                "input": input_tokens,
                "output": output_tokens,
                "total": total_tokens
            }
            
        # Extract segment latency stats
        latency_breakdown = output.get("latencies", {
            "schema": 0.0,
            "generation": 0.0,
            "execution": 0.0,
            "summarization": 0.0
        })
            
        return {
            "query": sql_query,
            "result": db_result,
            "summary": conversational_summary,
            "tokens": token_usage,
            "latency_breakdown": latency_breakdown,
            "model": getattr(self.llm, "model_name", getattr(self.llm, "model", "gpt-4o-mini")),
            "retries": output.get("retries", 0),
            "is_expert_matched": output.get("is_expert_matched", False),
            "lineage": output.get("lineage", {}),
            "estimated_cost": output.get("estimated_cost", 0.0),
            "retrieved_tables": output.get("retrieved_tables", []),
            "rag_savings_pct": output.get("rag_savings_pct", 0.0),
            "retrieved_few_shots": output.get("retrieved_few_shots", []),
            "scan_breakdown": output.get("scan_breakdown", []),
            "optimizer_advisories": output.get("optimizer_advisories", []),
            "raw_plan": output.get("raw_plan", "")
        }
