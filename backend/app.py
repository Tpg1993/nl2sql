import ast
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import inspect

from .agent import EHRQueryAgent

app = FastAPI(
    title="EHR SQL Agent API",
    description="API server for natural language querying of EHR data using LangGraph and LangChain.",
    version="1.0.0"
)

# Enable CORS for all origins (important for local development & frontend clients)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize agent
try:
    agent = EHRQueryAgent()
except Exception as e:
    print(f"Error initializing agent: {e}")
    # We will initialize it lazily in routes if needed, or raise
    agent = None


class QueryRequest(BaseModel):
    question: str


@app.get("/api/metadata")
def get_metadata():
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
def run_query(request: QueryRequest):
    """Query the agent with a natural language prompt."""
    if not agent:
        raise HTTPException(status_code=500, detail="Database agent is not initialized.")
    
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    try:
        res = agent.query_detailed(request.question)
        raw_result = res["result"]
        
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

        return {
            "query": res["query"],
            "result": parsed_data,
            "success": success,
            "error": error_message
        }
    except Exception as e:
        return {
            "error": str(e),
            "success": False
        }
