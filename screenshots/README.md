# EHR Query Agent — Implemented Functionalities & Screenshots

This folder contains the visual execution proof and user interface states for the implemented functionalities.

---

## 1. Abstract Syntax Tree (AST) SQL Safety Parser
* **Filename**: `01_AST_SQL_Safety_Validation_Warning.png`
* **Functionality**: Replaces regex-based SQL safety checks with an AST parser (`sqlglot`). Validates and blocks any modification queries (e.g. `DROP`, `DELETE`, `UPDATE`, `INSERT`, `ALTER`, `CREATE`) entered inside the metric formula dry-run validator or query console. Shows the pink warning modal on validation failure.

## 2. Interactive Semantic Configurator (Logical Schema Drawer)
* **Filename**: `02_Semantic_Configurator_Initial_Layout.png`
* **Functionality**: Provides a glassmorphic settings drawer for Administrators to configure the semantic layer entity attributes, field names, data classification rules, and relationship definitions.

## 3. Predefined Metrics Configurator & Live Dry-Run Compiler
* **Filename**: `03_Configurator_Predefined_Metrics_Tab.png`
* **Functionality**: Renders all predefined metrics inside a card deck. Provides a **Test Formula** compilation checker that triggers a mock execution against the database engine to report syntax errors or validation issues.

## 4. Configuration Backups (Import / Export JSON)
* **Filename**: `04_Configurator_Import_Export_Buttons.png`
* **Functionality**: Adds **Import** and **Export** buttons to the configurator drawer. Admins can download the active semantic configuration as a JSON file and upload existing JSON files to hot-reload settings.

## 5. Semantic Layer Database Auto-Discovery Prompt
* **Filename**: `05_Auto_Discover_Confirmation_Prompt.png`
* **Functionality**: The **Auto-Discover** button uses backend metadata routers (SQLAlchemy schema inspector) to scan the tables, primary/foreign keys, and data types, prompting the admin for validation before rewriting the schema.

## 6. Auto-Discovery Dynamic Save & Hot-Reload Alert
* **Filename**: `06_Auto_Discover_Saved_And_Hot_Reloaded.png`
* **Functionality**: Displays the hot-reload notifications using the unified status bar helper when schema definitions are updated on-disk.

## 7. Dynamic Compliance & PII Cell Masking (HIPAA)
* **Filename**: `07_PII_Cell_Masking_For_Researcher_Role.png`
* **Functionality**: Enforces dynamic masking rules based on the user's active RBAC role. Users logged in as `researcher` see PII names and emails masked with stars (`*`) and a locked cell icon in the tabular result view.

---

## 8. Immutable Audit Ledger — Log Table View
* **Filename**: `08_Immutable_Audit_Ledger_Log_Table.png`
* **Functionality**: Every NL-to-SQL query execution is appended as an immutable cryptographic record. The Audit Logs tab (under Semantic Configurator) displays a table showing: ID, Timestamp, Username, Role, Original Question, Generated SQL, and the SHA-256 Record Hash. Only `admin` role can view this panel.
* **Backend Proof**: `GET /api/config/audit-logs` returns `{"success": true, "logs": [...13 entries...]}` ✅
* **Unit Tests**: `backend/test_audit.py` — 3 tests PASSED (`test_write_and_chaining`, `test_tampering_detection`, `test_sqlglot_parsing`) ✅

## 9. Immutable Audit Ledger — Integrity Verification
* **Filename**: `09_Audit_Ledger_Integrity_Verified.png`
* **Functionality**: The "Verify Ledger Integrity" button triggers a full SHA-256 hash chain traversal from genesis block to the latest record. Any tampering (direct DB edit) is cryptographically detected.
* **Backend Proof**: `GET /api/config/verify-audit-ledger` returns `{"verified": true, "tampered_ids": [], "message": "Audit ledger integrity verified successfully."}` ✅
* **Chain Architecture**: Each record's hash is computed as `SHA256(timestamp|username|role|prompt|sql|tables|columns|latency|dataset_hash|previous_hash)` creating a linked-list blockchain.

## 10. Dynamic Few-Shot RAG Library Viewer
* **Filename**: `10_Dynamic_Few_Shot_RAG_Query_Library.png`
* **Functionality**: Integrates the few-shot query library browser directly inside the admin panel. Admins can visually inspect standard few-shot templates, matching scores, and target SQL statements used by the semantic selector.
* **E2E Tests**: `backend/test_selenium.py` — verified using automated UI browser steps. ✅

## 11. Dense Vector-based Metadata RAG Pruning
* **Filename**: `11_Vector_Metadata_RAG_Pruning.png`
* **Functionality**: Matches natural language queries to schemas using dense vector similarity scores. Matched tables are highlighted as purple monospace badges in the "Token & Latency Details" panel.
* **Unit & E2E Tests**: Verified using automated tests (`test_tfidf_fallback_graceful_run` and `test_13_dynamic_few_shot_and_vector_rag_details_panel`). ✅

## 12. Resource Group Bypass Configurator Tab
* **Filename**: `12_Bypass_Groups_Configurator_Tab.png`
* **Functionality**: A configuration interface under the Semantic Configurator where Administrators can assign user roles to the `HighPerformanceQueryGroup`, allowing them to completely bypass query cost limits.
* **UI Integration & E2E Validation**: Verified via Selenium automated testing (`test_14`). ✅

## 13. Cost-Aware Safety Guardrail (Query Blocked)
* **Filename**: `13_Cost_Safety_Blocked_Query.png`
* **Functionality**: When a normal role (e.g. `researcher`) executes a query estimated to scan database rows beyond safe thresholds (e.g. a full scan on a large table), the query planner intercepts it and displays a cost safety error bubble warning.
* **E2E Tests**: Verified via Selenium automated testing (`test_15`). ✅

## 14. Cost-Aware Safety Guardrail Bypass (Query Succeeded)
* **Filename**: `14_Cost_Safety_Bypass_Success.png`
* **Functionality**: When an approved role (e.g. `doctor`, who was added to the `HighPerformanceQueryGroup` by the admin) runs the exact same heavy query, the execution planner bypasses the safety guardrails, executing it and rendering the dynamic results table.
* **E2E Tests**: Verified via Selenium automated testing (`test_15`). ✅
