# Roadmap: Proposed Enterprise Enhancements

This document outlines the planned future enhancements and next-generation features for the NL2SQL Agentic Platform.

---

## 1. UI/UX & Interactive Analytics

### 1.1 Sidebar Session Categorization & Search
* **Date-based Grouping**: Categorize chat history cards in the left sidebar under logical groups (e.g., "Today", "Yesterday", "Previous 7 Days") similar to Gemini/ChatGPT.
* **Search History**: Implement a client-side search bar at the top of the "Chats" panel to filter saved sessions by title or timestamp.

### 1.2 Enhanced Charting & Analytics Panel
* **Advanced Chart Types**: Extend Chart.js integration to support Scatter plots, Heatmaps, and Radar charts for complex medical/demographic datasets.
* **Direct Exporting**: Add buttons inside the query result cards to download datasets as **CSV** or formatted **Excel** sheets directly from the frontend.

### 1.3 Interactive RLHF Feedback Widget
* **Analyst Annotations**: Let non-admin users flag generated SQL queries with thumbs-up/down feedback and attach comments.
* **Correction Pipeline**: Flagged queries are routed to an admin dashboard for quick verification and conversion into Expert SQL overrides.

---

## 2. Security, Role & Compliance Hardening

### 2.1 Dynamic Row-Level ABAC Policies
* **Dynamic Scoping**: Replace hardcoded role/department mappings with a user-attribute criteria system defined dynamically in the Semantic Layer (e.g., allowing doctors to view records matching both their department and region attributes).
* **Dynamic Column Hashing**: Add options to hash PII column values (e.g. salt + SHA-256) rather than masking with asterisks, enabling researchers to run aggregates on distinct patients without exposing identifiers.

### 2.2 Tamper-Alert Audit Daemon
* **Verification Agent**: Implement a background service that regularly scans the `audit_ledger.db` verification hashes and alerts compliance officers (via Email/Webhooks) if cryptographic tampering is detected.

---

## 3. Database Routing & Federated Optimization

### 3.1 Advanced Join Pushdowns
* **Parallel Execution**: Execute local SQLite subqueries and remote Databricks queries in parallel threads to reduce round-trip latency on federated queries.
* **Adaptive Semi-Join Pushdowns**: Automatically choose between standard pushdown constrains and hash-joins based on the cardinality of the candidate join key dataset.

### 3.2 Dynamic Database Engine Routing
* **Multi-tenant Routing**: Dynamically switch the target database connection URL based on the authenticated user's organization or tenant ID decoded from the JWT token.

---

## 4. LLM & Semantic Layer Intelligence

### 3.1 Vector Database Few-Shot Library
* **Scalable RAG**: Replace the local TF-IDF matcher with a vector database index (e.g. Chroma/Pinecone) for low-latency similarity searches across thousands of curated few-shot examples.
* **Auto-Discovery of Metrics**: Periodically parse query history logs to automatically recommend new metric definitions to the Semantic Layer configuration.
