# Version History & Releases

This document tracks the feature additions, database routing, model integrations, and caching changes across different development branches.

---

## [v4_sarvam_enhanced_agent](file:///c:/Users/Tejas/Downloads/APPS/NL2SQL/versions.md#v4_sarvam_enhanced_agent) (Current Branch)
*   **Release Focus**: Self-healing agent loops, clinical summaries, latency metrics, interactive schema browser, and syntax highlighting.
*   **Self-Healing Loop**: Tracks query failures using a custom `AgentState` retry counter. If SQL execution fails, the agent routes back to the LLM with error context to heal the syntax automatically.
*   **Conversational Summaries**: Adds a `summarize_results` node to synthesize raw table outcomes into natural language clinical summaries.
*   **Performance Tracking**: Computes millisecond database latencies, showing real-time latencies on-screen.
*   **Stage Latency Breakdown**: Displays detailed segment-level latency metrics (cache retrieval, schema reflection, SQL generation, database query execution, and conversational summarization) formatted to exactly 2 decimal places within a collapsible statistics panel.
*   **Hover Info Tooltips**: Uses high-performance pure-CSS hover tooltips next to main and stage latencies, showing contextual explanations on hover using FontAwesome info icons.
*   **Agent Execution Context**: Displays the active LLM Model Engine and the number of Self-Healing SQL query retries in both the main metadata bar and the expanded token stats details panel.
*   **Interactive Sidebar**: Clicking any sidebar table or column appends it directly to the chat input and focuses it.
*   **SQL Syntax Highlighting**: Custom regex SQL tokenizer dynamically colors keywords, strings, and integers for maximum readability.

---

## [v3_sarvam_redis_cache](file:///c:/Users/Tejas/Downloads/APPS/NL2SQL/versions.md#v3_sarvam_redis_cache)
*   **Release Focus**: Production-ready hybrid query caching, cost-savings visuals, and UI theme controls.
*   **Database Engine**: Works on both local SQLite and remote Databricks SQL Warehouse.
*   **Caching Layer**:
    *   Dynamic checking for `REDIS_URL` in the environment.
    *   If active, boots distributed **Redis** caching to speed up recurring queries and save execution costs.
    *   Cascades automatically back to a local programmatically-evicted **SQLite** cache if Redis is unconfigured or offline.
*   **Token Savings Visuals**: API responses flag `"cached": True` on hits, updating the UI panel to green highlights, listing saved token metrics, and displaying a cost-saving notification banner.
*   **Theme Switcher Controls**: Dynamic Light and Dark themes with full support for glassmorphic elements and local storage theme persistence.
*   **Dependencies**: Added `redis==5.0.4`.

---

## [v2_sarvam_databricks_sql](file:///c:/Users/Tejas/Downloads/APPS/NL2SQL/versions.md#v2_sarvam_databricks_sql)
*   **Release Focus**: Cloud-scale remote database connections.
*   **Database Engine**: Integrates a dynamic DB Router that boots a remote **Databricks SQL Warehouse** connection if credentials (`DATABRICKS_HOST`, `DATABRICKS_TOKEN`, and `DATABRICKS_HTTP_PATH`) are present in `.env`.
*   **Fallback**: Dynamically falls back to local SQLite database ([backend/ehr_data.db](file:///c:/Users/Tejas/Downloads/APPS/NL2SQL/backend/ehr_data.db)) if credentials are left blank.
*   **Dependencies**: Added `databricks-sqlalchemy==2.0.9`.

---

## [v1_sarvam_local_sql](file:///c:/Users/Tejas/Downloads/APPS/NL2SQL/versions.md#v1_sarvam_local_sql)
*   **Release Focus**: Baseline EHR Agentic NL2SQL implementation.
*   **Database Engine**: Single-node SQLite database ([backend/ehr_data.db](file:///c:/Users/Tejas/Downloads/APPS/NL2SQL/backend/ehr_data.db)).
*   **Model Router**: Implements the core LangGraph sequential pipeline (`list_tables` -> `get_schema` -> `generate_query` -> `execute_query`). Primary generation runs on Sarvam AI (`sarvam-105b`) when `SARVAM_API_KEY` is present, with backup fallback to OpenAI (`gpt-4o-mini`).
