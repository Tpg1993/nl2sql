# Version History & Releases

This document tracks the feature additions, database routing, model integrations, and caching changes across different development branches.

---

## [v7_sarvam_enterprise_suite](#v7_sarvam_enterprise_suite) (Current Branch)
*   **Release Focus**: Decoupled Semantic Layer, Decoupled Prompt Library, Cost-Aware Execution Planner, Federated Querying, Lineage-Backed Audit Trails, and Expert Override (RLHF) Loop.
*   **Semantic Layer**: Integrates a `semantic_layer.yaml` mapping business metrics and entities (e.g. "patient summary counts") to database columns and tables. Pre-compiles logical definitions into LLM context prompts, ensuring generated SQL queries align with organizational metrics.
*   **Decoupled Prompt Library**: Extracts hardcoded LLM prompts from Python agent code into parameterizable flat text files under `backend/prompts/` (e.g. `sql_generation.txt`, `summarization.txt`). Managed by a dynamic `PromptLibrary` loader, this allows easily adapting the agent core to other business domains by simply switching the text templates.
*   **Cost-Aware Execution Planner**: Intercepts expensive queries by analyzing SQLite/Databricks query execution plans (`EXPLAIN`). Blocks Cartesian products or high-cost scans before execution, preventing database overloading.
*   **Federated Querying**: Implements a router to handle distributed queries, dynamically joining local SQLite tables (e.g., patient metadata) with simulated remote Databricks SQL Warehouse tables.
*   **Lineage-Backed Audit Trails**: Automatically parses generated SQL to extract table/column dependencies, joins, and filters. Visualizes query lineage pathways in the frontend, providing clinical compliance tracking.
*   **Expert Override (RLHF) Loop**: Allows administrators/analysts to suggest SQL query corrections. Persists analyst-approved queries in a localized SQLite database (`cache.db`), immediately serving corrected SQL on match and showcasing an "Expert Approved" badge.

---

## [v6_sarvam_auth_api](#v6_sarvam_auth_api)
*   **Release Focus**: JWT OAuth2 authentication, endpoint rate-limiting, restricted CORS access whitelists, input constraint filters, and glassmorphic login overlays.
*   **JWT OAuth2 Authentication**: Implements bearer-token validation (`HS256`) protecting database metadata reflection and query generation endpoints. Decodes and verifies token signatures and expirations locally.
*   **Endpoint Rate-Limiting**: Integrates `slowapi` to restrict access burst frequency (5/min login, 15/min query, 30/min schema metadata) to mitigate denial-of-service and token cost exhaustion.
*   **Restricted CORS & Whitelisting**: Transitioned backend CORS configuration from global wildcard (`*`) to comma-separated domains loaded via `ALLOWED_ORIGINS` in `.env`.
*   **Query Input Constraints**: Hardens the query handler by validating and rejecting questions exceeding 500 characters to protect LLM contexts.
*   **Glassmorphic Login UX**: Implements client-side session redirection logic and a password entry card overlay fully integrated into the existing dark/light theme switchers.

---

## [v5_sarvam_sql_guardrails](#v5_sarvam_sql_guardrails)
*   **Release Focus**: SQL security guardrails, read-only engine hardening, query limit constraints, safe error handling, and unified Databricks/SQLite compatibility.
*   **SQL Safety Auditor**: Introduces a regex-based pre-execution audit block (`audit_sql_query`) to intercept write, drop, or schema alteration keywords (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, etc.) outside SQL string literals. Applicable to both SQLite and Databricks.
*   **Read-Only Engine Hardening**: Configures SQLite database connections using URI parameters (`mode=ro` with `uri=True`) to block modifications at the database engine level, raising engine-level `OperationalError` upon write attempts.
*   **Default SELECT Query Limits**: Automatically wraps `SELECT` queries missing `LIMIT` constraints with a default `LIMIT 100` boundary to protect server memory and browser rendering performance. Fully compatible with Databricks SQL.
*   **Safe Error Handling & Graph Routing**: Catches security exceptions, SQLite engine write errors, and Databricks database permission violations (`Permission denied`, `does not have privilege`, `UNAUTHORIZED`) within the graph, bypasses LLM summarization, and instantly routes to returning a structured static security warning.

---

## [v4_sarvam_enhanced_agent](#v4_sarvam_enhanced_agent)
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

## [v3_sarvam_redis_cache](#v3_sarvam_redis_cache)
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

## [v2_sarvam_databricks_sql](#v2_sarvam_databricks_sql)
*   **Release Focus**: Cloud-scale remote database connections.
*   **Database Engine**: Integrates a dynamic DB Router that boots a remote **Databricks SQL Warehouse** connection if credentials (`DATABRICKS_HOST`, `DATABRICKS_TOKEN`, and `DATABRICKS_HTTP_PATH`) are present in `.env`.
*   **Fallback**: Dynamically falls back to local SQLite database ([backend/ehr_data.db](backend/ehr_data.db)) if credentials are left blank.
*   **Dependencies**: Added `databricks-sqlalchemy==2.0.9`.

---

## [v1_sarvam_local_sql](#v1_sarvam_local_sql)
*   **Release Focus**: Baseline EHR Agentic NL2SQL implementation.
*   **Database Engine**: Single-node SQLite database ([backend/ehr_data.db](backend/ehr_data.db)).
*   **Model Router**: Implements the core LangGraph sequential pipeline (`list_tables` -> `get_schema` -> `generate_query` -> `execute_query`). Primary generation runs on Sarvam AI (`sarvam-105b`) when `SARVAM_API_KEY` is present, with backup fallback to OpenAI (`gpt-4o-mini`).
