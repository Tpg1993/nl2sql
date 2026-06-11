# Version History & Releases

This document tracks the feature additions, database routing, model integrations, and caching changes across different development branches.

---

## [v20_sarvam_federated_query_router](#v20_sarvam_federated_query_router) (Planned Branch)
*   **Release Focus**: Federated Query Virtualization across multiple SQLite/Cloud Databricks databases.

## [v19_sarvam_gitops_pr_sync](#v19_sarvam_gitops_pr_sync) (Planned Branch)
*   **Release Focus**: GitOps PR sync automation directly from the Semantic Configurator console UI.

## [v18_sarvam_explain_cost_guardrails](#v18_sarvam_explain_cost_guardrails) (Planned Branch)
*   **Release Focus**: Explain-based cost safety query execution planners and DB optimizer checks.

## [v17_vector_metadata_rag](#v17_vector_metadata_rag) (Current Active Branch)
*   **Release Focus**: Dense Vector-based Metadata RAG for scalable table schema lookup.
*   **Vector Search & Caching**: Transitioned table schema search from keyword matching to dense vector embeddings (`text-embedding-3-small`). Embeds all schemas on startup, caching them in memory for sub-millisecond retrieval latency.
*   **Local TF-IDF Fallback**: Preserves offline compatibility by falling back to local TF-IDF Cosine similarity math if OpenAI API key is missing or request fails.
*   **UI Integration & E2E Validation**: Displays matched tables as badges inside the Token & Latency Details drawer. Verified via Selenium automated testing (`test_13`).

## [v16_dynamic_few_shot_rag](#v16_dynamic_few_shot_rag)
*   **Release Focus**: Dynamic Few-Shot RAG Query Library using embedding-based similarity matches.
*   **Semantic Few-Shot Matcher**: Matches clinical questions against a local library of curated few-shot examples using embeddings to find the most relevant context.
*   **UI Library view**: Allows admins to view the few-shot query library dynamically.

## [v15_immutable_audit_ledger](#v15_immutable_audit_ledger)
*   **Release Focus**: HIPAA-compliant immutable audit logs.
*   **Cryptographic Chain**: Pairs all queries with a SHA-256 hash chaining mechanism ensuring data query audit logs are tamper-evident.
*   **Admin Logs Verification UI**: Visual ledger log browser and chain integrity verification endpoint.

## [v14_ast_safety_parser](#v14_ast_safety_parser)
*   **Release Focus**: Abstract Syntax Tree (AST) query structure parser for database write safety.
*   **SQLGlot Validation**: Validates query structures against a safe-AST schema validator, explicitly blocking modifications.

## [v13_sarvam_interactive_semantic_builder](#v13_sarvam_interactive_semantic_builder)
*   **Release Focus**: Interactive Configurator Editor V2.
*   **Auto-Discovery Integration**: Automatically parses schema relations and infers column classification rules from active DB.
*   **JSON Config Import/Export**: Enables visual layout backups and migrations.
*   **Metrics Compiler Checks**: Implements client-side dry-run test formula compiles.

## [v12_sarvam_semantic_layer_editor](#v12_sarvam_semantic_layer_editor)
*   **Release Focus**: Baseline config editor console endpoints.

---

## [v11_sarvam_metadata_rag](#v11_sarvam_metadata_rag) (Current Branch)
*   **Release Focus**: Metadata RAG & Large-Scale Schema Reflection.
*   **Scalable Schema Reflection**: Replaces global listing and parsing of all database schemas with a dynamic `retrieve_schema_node` utilizing a custom `MetadataRAG` system. Ensures the agent scales gracefully to schemas with thousands of tables.
*   **TF-IDF & Cosine Similarity Matcher**: Implements indexing of table names, columns, semantic entities, relationships, and metrics, combined with custom singular/plural stemming.
*   **Relational Semantic Expansion**: Integrates graph-based boosting to automatically pull in connected parent/child tables (e.g. `patients`) when related tables (e.g. `medications`, `vitals`) score highly.
*   **API & UI Integration**: Returns `retrieved_tables` list in query JSON responses (including cache hits/misses) and displays them as monospace purple badges under the Token & Latency Details panel.

---

## [v10_sarvam_selenium_tests](#v10_sarvam_selenium_tests)
*   **Release Focus**: End-to-End Automated Integration Test Suite using Selenium WebDriver.
*   **Headless Execution**: Runs E2E integration test suite in `backend/test_selenium.py` using Chrome in `--headless` mode, ensuring full compatibility with CI/CD and CLI-only environments.
*   **Test Cases**: Validates E2E functionality across 9 separate test scenarios:
    1. JWT Authentication Overlay (invalid login validation).
    2. Role-Based Access Control UI (Role Badge display).
    3. Interactive Schema Sidebar Browser (clicking tables to inject in search input).
    4. Collapsible Sidebar Panel (local storage state persistence and transitions).
    5. Theme Switcher (data-theme toggling and persistence).
    6. Dynamic Query execution and caching (standard Cache hit badge).
    7. Query Lineage Audit Logs (extraction and rendering of lineage source table badges).
    8. RLHF Expert SQL Overrides (override persistence and green Expert Approved badge).
    9. HIPAA Compliance & PII Masking (dynamic name column masking and lock icons for Researcher role).
*   **Automation Harness**: Automatically manages FastAPI backend uvicorn and frontend server lifecycles, polls endpoints to verify readiness, clears the database cache before runs to maintain isolation, and captures failure screenshots.

---

## [v7_sarvam_enterprise_suite](#v7_sarvam_enterprise_suite)
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
