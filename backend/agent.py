import os
import time
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
        """Node 3: Generate SQL query based on user question and DB schema, taking error details on retry."""
        print("\n[Node 3] Building execution query context...")
        start_time = time.perf_counter()
        user_question = state["messages"][0].content
        
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

        prompt = f"""You are an SQL database query expert. Generate the correct query matching the schema below.
        
        User Query Request: {user_question}
        
        Database Schema Context:
        {db_schema}
        
        Rules:
        - Use ONLY safe SELECT operations. Do not update, alter, append, or drop tables.
        - Return ONLY the clean, raw SQL string payload. Do not wrap it in markdown framing like ```sql."""

        if previous_error:
            prompt += f"""

            WARNING: The SQL query you previously generated failed with the following execution error:
            {previous_error}

            Analyze the error and the schema closely. Generate a corrected SQL query using only valid, existing column names and SQL syntax. Avoid repeating the same mistake."""
        
        result = self.llm.invoke(prompt)
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        
        latencies = state.get("latencies", {}).copy()
        latencies["generation"] = latencies.get("generation", 0.0) + elapsed_ms
        
        print(f"-> Active Model Prompt Output: {result.content}")
        return {"messages": [result], "latencies": latencies}

    def execute_query_node(self, state: AgentState) -> Dict[str, Any]:
        """Node 4: Execute SQL query on the database, with exception tracking for retry router."""
        print("\n[Node 4] Injecting context payload into DB engine...")
        start_time = time.perf_counter()
        sql_query = str(state["messages"][-1].content).strip()
        
        # Safe extraction if state messages are out of order
        if sql_query.startswith("Error executing query:"):
            for msg in reversed(state["messages"]):
                content = str(msg.content).strip()
                if not content.startswith("Error executing query:") and "SELECT" in content.upper():
                    sql_query = content
                    break
        
        try:
            result = self.run_query_tool.invoke(sql_query)
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            
            latencies = state.get("latencies", {}).copy()
            latencies["execution"] = latencies.get("execution", 0.0) + elapsed_ms
            return {"messages": [AIMessage(content=result)], "latencies": latencies}
        except Exception as e:
            result = f"Error executing query: {str(e)}"
            print(f"-> Query Execution Failed: {result}")
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            
            latencies = state.get("latencies", {}).copy()
            latencies["execution"] = latencies.get("execution", 0.0) + elapsed_ms
            current_retries = state.get("retries", 0)
            return {
                "messages": [AIMessage(content=result)],
                "retries": current_retries + 1,
                "latencies": latencies
            }

    def should_retry(self, state: AgentState) -> str:
        """Conditional edge router checking retry limit on query execution errors."""
        last_msg = state["messages"][-1].content
        retries = state.get("retries", 0)
        
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
            "retries": output.get("retries", 0)
        }
