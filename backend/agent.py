import os
import time
import re
import sqlite3
from typing import Any, Dict, List
from sqlalchemy import create_engine
from langchain_community.utilities import SQLDatabase
from langchain_openai import ChatOpenAI
from langgraph.graph import MessagesState, StateGraph, START, END
from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langchain_core.messages import AIMessage, HumanMessage

# Load environment variables from .env file if available
try:
    from dotenv import load_dotenv
    # Look for .env in the same directory as this file
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass

class AgentState(MessagesState):
    """Custom LangGraph state incorporating message history, retry count, and section latencies."""
    retries: int
    latencies: Dict[str, float]
    is_expert_matched: bool
    estimated_cost: float
    lineage: Dict[str, Any]

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

        # Initialize semantic layer, planner, and override cache store
        from .semantic_layer import SemanticLayer
        from .planner import CostPlanner
        from .expert_overrides import ExpertOverrideStore
        self.semantic_layer = SemanticLayer()
        self.cost_planner = CostPlanner(self.engine)
        self.override_store = ExpertOverrideStore()

        # Build and compile LangGraph state machine
        self.agent = self._compile_graph()

    def _init_llm(self) -> ChatOpenAI:
        """Initializes the LLM, defaulting to Sarvam AI if API key is present,
        otherwise cascading to OpenAI backup layer.
        """
        sarvam_key = os.environ.get("SARVAM_API_KEY")
        if sarvam_key:
            print("\n[LLM Router] SARVAM_API_KEY discovered. Booting Sarvam AI as Primary...")
            return ChatOpenAI(
                model="sarvam-105b",
                openai_api_key=sarvam_key,
                openai_api_base="https://api.sarvam.ai/v1",
                temperature=0
            )
        
        print("\n[LLM Router] No Sarvam Key found. Cascading to OpenAI Backup Layer...")
        return ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0
        )

    def list_tables_node(self, state: AgentState) -> Dict[str, Any]:
        """Node 1: Fetch the available catalog tables."""
        print("\n[Node 1] Fetching catalog tables...")
        start_time = time.perf_counter()
        result = self.list_tables_tool.invoke("")
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        
        latencies = state.get("latencies", {}).copy()
        latencies["schema"] = latencies.get("schema", 0.0) + elapsed_ms
        return {"messages": [AIMessage(content=result)], "latencies": latencies}

    def get_schema_node(self, state: AgentState) -> Dict[str, Any]:
        """Node 2: Read specifications/schemas for the tables."""
        print("\n[Node 2] Reading schema specifications...")
        start_time = time.perf_counter()
        table_names = state["messages"][-1].content
        result = self.get_schema_tool.invoke(table_names)
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        
        latencies = state.get("latencies", {}).copy()
        latencies["schema"] = latencies.get("schema", 0.0) + elapsed_ms
        return {"messages": [AIMessage(content=result)], "latencies": latencies}

    def generate_query_node(self, state: AgentState) -> Dict[str, Any]:
        """Node 3: Generate SQL query based on user question, DB schema, and semantic layer, taking error details on retry."""
        print("\n[Node 3] Building execution query context...")
        start_time = time.perf_counter()
        user_question = state["messages"][0].content
        
        # 1. Pre-execution expert override check (RLHF)
        override_sql = self.override_store.get_override(user_question)
        if override_sql:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            latencies = state.get("latencies", {}).copy()
            latencies["generation"] = latencies.get("generation", 0.0) + elapsed_ms
            return {
                "messages": [AIMessage(content=override_sql)],
                "latencies": latencies,
                "is_expert_matched": True
            }

        # Locate schema description in message history
        db_schema = ""
        for msg in reversed(state["messages"]):
            if isinstance(msg, AIMessage) and "CREATE TABLE" in msg.content:
                db_schema = msg.content
                break
        if not db_schema:
            db_schema = state["messages"][2].content if len(state["messages"]) > 2 else ""

        # Identify previous execution errors for self-healing prompt injections
        retries = state.get("retries", 0)
        previous_error = ""
        if retries > 0:
            last_msg = state["messages"][-1].content
            if last_msg.startswith("Error executing query:"):
                previous_error = last_msg

        prompt = f"""You are an SQL database query expert. Generate the correct query matching the schema and semantic layer rules below.
        
        User Query Request: {user_question}
        
        Database Semantic Layer Context (Business logic definitions, calculations, and join keys):
        {self.semantic_layer.get_context_prompt()}
        
        Database Schema Context (Raw table schema details):
        {db_schema}
        
        Rules:
        - Use ONLY safe SELECT operations. Do not update, alter, append, or drop tables.
        - Return ONLY the clean, raw SQL string payload. Do not wrap it in markdown framing like ```sql.
        - Prioritize using the join relationships defined in the semantic layer relationships.
        - Prioritize formulas in the metrics list for calculations (e.g. active allergies calculation).
        """

        if previous_error:
            prompt += f"""

            WARNING: The SQL query you previously generated failed with the following execution error:
            {previous_error}

            Analyze the error, raw schema, and semantic layer. Generate a corrected SQL query using only valid, existing column names and SQL syntax. Avoid repeating the same mistake."""
        
        result = self.llm.invoke(prompt)
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        
        latencies = state.get("latencies", {}).copy()
        latencies["generation"] = latencies.get("generation", 0.0) + elapsed_ms
        
        print(f"-> Active Model Prompt Output: {result.content}")
        return {"messages": [result], "latencies": latencies, "is_expert_matched": False}

    @staticmethod
    def strip_sql_literals(sql: str) -> str:
        """Removes single and double quoted string literals from SQL query to avoid false positives on keywords."""
        sql_no_singles = re.sub(r"'[^']*(?:''[^']*)*'", " ", sql)
        sql_no_doubles = re.sub(r'"[^"]*(?:""[^"]*)*"', " ", sql_no_singles)
        return sql_no_doubles

    def audit_sql_query(self, sql_query: str) -> None:
        """Audits the generated SQL query for dangerous write/schema alteration operations."""
        DANGEROUS_KEYWORDS = [
            "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", 
            "REPLACE", "TRUNCATE", "GRANT", "REVOKE", "PRAGMA"
        ]
        cleaned_sql = self.strip_sql_literals(sql_query)
        for kw in DANGEROUS_KEYWORDS:
            pattern = r"\b" + kw + r"\b"
            if re.search(pattern, cleaned_sql, re.IGNORECASE):
                raise ValueError(f"Security violation: Disallowed SQL command keyword '{kw}' detected outside string literals.")

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
        
        try:
            # Security Guardrail Check
            self.audit_sql_query(sql_query)

            # Cost Planner Check
            is_safe, cost_reason, cost_metrics = self.cost_planner.analyze_query(sql_query)
            if not is_safe:
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
            
            return {
                "messages": [AIMessage(content=result)],
                "latencies": latencies,
                "lineage": lineage_data,
                "estimated_cost": cost_metrics.get("scanned_tables_count", 1) * 10.0
            }
        except Exception as e:
            err_str = str(e)
            err_str_lower = err_str.lower()
            is_security = (
                "security violation" in err_str_lower or
                "readonly database" in err_str_lower or
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
            return {
                "messages": [AIMessage(content=result)],
                "retries": next_retries,
                "latencies": latencies
            }

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
        for msg in reversed(state["messages"]):
            content = msg.content
            if content.startswith("Error executing query:"):
                db_result = content
                break
            elif not db_result:
                db_result = content
                break

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

        # Truncate database result if it exceeds a safe size (e.g. 5000 characters)
        if len(db_result) > 5000:
            print(f"-> Truncating large database result from {len(db_result)} to 5000 characters to prevent API payload limits.")
            db_result = db_result[:5000] + "\n... [Truncated for LLM payload size limits]"

        if db_result.startswith("Error executing query:"):
            prompt = f"""Write a brief, polite response explaining that we couldn't resolve the database query due to an execution error.
            
            Original Question: {user_question}
            Error Details: {db_result}"""
        else:
            prompt = f"""You are a clinical data summarizer. Write a clean, brief natural language response answering the user's clinical question based directly on the database query results. Keep it simple and direct. Do not explain SQL syntax or mention table names.
            
            User Question: {user_question}
            Database Result: {db_result}"""
            
        result = self.llm.invoke(prompt)
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        
        latencies = state.get("latencies", {}).copy()
        latencies["summarization"] = latencies.get("summarization", 0.0) + elapsed_ms
        
        print(f"-> Conversational Summary: {result.content}")
        return {"messages": [result], "latencies": latencies}

    def is_federated_query(self, sql_query: str) -> bool:
        """Determines if a query requires federated join between local SQLite and remote Databricks SQL."""
        has_databricks = os.environ.get("DATABRICKS_HOST") is not None
        query_upper = sql_query.upper()
        return has_databricks and "PATIENTS" in query_upper and "DEPARTMENTS" in query_upper

    def execute_federated_query(self, sql_query: str) -> str:
        """Executes federated query by querying SQLite and Databricks separately, and joining outcomes in-memory."""
        print("-> Running Federated Join across local SQLite and remote Databricks...")
        try:
            import sqlite3
            BASE_DIR = os.path.dirname(os.path.abspath(__file__))
            sqlite_db = os.path.join(BASE_DIR, "ehr_data.db")
            sqlite_conn = sqlite3.connect(sqlite_db)
            sqlite_cursor = sqlite_conn.cursor()
            
            # Fetch patients (limit to 10 for safety)
            sqlite_cursor.execute("SELECT patient_id, full_name, gender, dob FROM patients LIMIT 10")
            patients = sqlite_cursor.fetchall()
            sqlite_conn.close()
            
            # Fetch departments from remote Databricks SQL engine
            with self.engine.connect() as db_conn:
                db_result = db_conn.execute("SELECT department_id, department_name, location FROM departments LIMIT 5").fetchall()
            
            # Simulate polystore client-side join merge
            unified_rows = []
            for pat in patients:
                dept = db_result[pat[0] % len(db_result)] if db_result else (1, "Default Dept", "Local")
                unified_rows.append((pat[1], pat[2], pat[3], dept[1], dept[2]))
            
            return str(unified_rows)
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
        builder.add_node("list_tables", self.list_tables_node)
        builder.add_node("get_schema", self.get_schema_node)
        builder.add_node("generate_query", self.generate_query_node)
        builder.add_node("execute_query", self.execute_query_node)
        builder.add_node("summarize_results", self.summarize_results_node)
        
        # Configure transitions
        builder.add_edge(START, "list_tables")
        builder.add_edge("list_tables", "get_schema")
        builder.add_edge("get_schema", "generate_query")
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

    def query(self, question: str) -> str:
        """Runs the compiled graph workflow for a natural language question."""
        initial_state = {
            "messages": [HumanMessage(content=question)],
            "retries": 0,
            "latencies": {
                "schema": 0.0,
                "generation": 0.0,
                "execution": 0.0,
                "summarization": 0.0
            }
        }
        output = self.agent.invoke(initial_state)
        return output["messages"][-1].content

    def query_detailed(self, question: str) -> dict:
        """Runs the compiled graph workflow and returns generated SQL, DB results, conversational summary, and LLM token usage."""
        initial_state = {
            "messages": [HumanMessage(content=question)],
            "retries": 0,
            "latencies": {
                "schema": 0.0,
                "generation": 0.0,
                "execution": 0.0,
                "summarization": 0.0
            }
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
            "estimated_cost": output.get("estimated_cost", 0.0)
        }
