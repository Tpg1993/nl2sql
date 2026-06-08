import os
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

class EHRQueryAgent:
    """An agent that translates natural language queries to SQL, executes them 
    against an SQLite database, and returns the query results using a LangGraph workflow.
    """

    def __init__(self, db_uri: str = None) -> None:
        # Resolve DB path dynamically if not provided
        if db_uri is None:
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

    def list_tables_node(self, state: MessagesState) -> Dict[str, List[AIMessage]]:
        """Node 1: Fetch the available catalog tables."""
        print("\n[Node 1] Fetching catalog tables...")
        result = self.list_tables_tool.invoke("")
        return {"messages": [AIMessage(content=result)]}

    def get_schema_node(self, state: MessagesState) -> Dict[str, List[AIMessage]]:
        """Node 2: Read specifications/schemas for the tables."""
        print("\n[Node 2] Reading schema specifications...")
        table_names = state["messages"][-1].content
        result = self.get_schema_tool.invoke(table_names)
        return {"messages": [AIMessage(content=result)]}

    def generate_query_node(self, state: MessagesState) -> Dict[str, List[Any]]:
        """Node 3: Generate SQL query based on user question and DB schema."""
        print("\n[Node 3] Building execution query context...")
        user_question = state["messages"][0].content
        db_schema = state["messages"][-1].content
        
        prompt = f"""You are an SQL database query expert. Generate the correct query matching the schema below.
        
        User Query Request: {user_question}
        
        Database Schema Context:
        {db_schema}
        
        Rules:
        - Use ONLY safe SELECT operations. Do not update, alter, append, or drop tables.
        - Return ONLY the clean, raw SQL string payload. Do not wrap it in markdown framing like ```sql."""
        
        result = self.llm.invoke(prompt)
        print(f"-> Active Model Prompt Output: {result.content}")
        return {"messages": [result]}

    def execute_query_node(self, state: MessagesState) -> Dict[str, List[AIMessage]]:
        """Node 4: Execute SQL query on the database, with exception handling."""
        print("\n[Node 4] Injecting context payload into DB engine...")
        sql_query = str(state["messages"][-1].content).strip()
        try:
            result = self.run_query_tool.invoke(sql_query)
        except Exception as e:
            result = f"Error executing query: {str(e)}"
            print(f"-> Query Execution Failed: {result}")
        return {"messages": [AIMessage(content=result)]}

    def _compile_graph(self) -> Any:
        """Sets up the state graph nodes, edges, and compiles it."""
        builder = StateGraph(MessagesState)
        
        # Register nodes
        builder.add_node("list_tables", self.list_tables_node)
        builder.add_node("get_schema", self.get_schema_node)
        builder.add_node("generate_query", self.generate_query_node)
        builder.add_node("execute_query", self.execute_query_node)
        
        # Configure transitions
        builder.add_edge(START, "list_tables")
        builder.add_edge("list_tables", "get_schema")
        builder.add_edge("get_schema", "generate_query")
        builder.add_edge("generate_query", "execute_query")
        builder.add_edge("execute_query", END)
        
        return builder.compile()

    def query(self, question: str) -> str:
        """Runs the compiled graph workflow for a natural language question."""
        initial_state = {"messages": [HumanMessage(content=question)]}
        output = self.agent.invoke(initial_state)
        return output["messages"][-1].content

    def query_detailed(self, question: str) -> dict:
        """Runs the compiled graph workflow and returns both generated SQL and DB results."""
        initial_state = {"messages": [HumanMessage(content=question)]}
        output = self.agent.invoke(initial_state)
        
        # The last message is the DB output, the second-to-last is the SQL query
        sql_query = ""
        db_result = ""
        if len(output["messages"]) >= 2:
            sql_query = output["messages"][-2].content
        if len(output["messages"]) >= 1:
            db_result = output["messages"][-1].content
            
        return {
            "query": sql_query,
            "result": db_result
        }
