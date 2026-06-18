# Generalized Role-Feature Matrix

This document maps all core features of the NL2SQL Agent application between **Admins** and **Non-Admins** (General Users).

| Feature Area | Admins (`admin` role) | Non-Admins (General Users) | Access Rules & Enforcement Details |
| :--- | :---: | :---: | :--- |
| **NL2SQL Query Interface** | **Enabled** (Full Access) | **Enabled** (Policy Masked) | Standard UI panel for asking natural language questions and executing database queries. |
| **Left Sidebar Chat Navigation** | **Enabled** | **Enabled** | Tabbed sidebar for viewing past conversation cards and browsing database schema tables. |
| **Conversational Memory** | **Enabled** | **Enabled** | Persists session thread contexts (sliding window of last 3 turns) in checkpointer memory. |
| **Dynamic Visual Charting** | **Enabled** | **Enabled** | Automatically detects numeric columns in query results and enables dynamic Chart.js visualizations. |
| **Security & PII Masking** | **Bypassed** | **Enforced** | Non-admin query results undergo automated dynamic masking (e.g. asterisks for sensitive columns) based on compliance configuration rules. |
| **Query Cost Safety Guardrails** | **Bypassed** | **Enforced** | Checks query plans prior to execution and blocks resource-intensive queries unless bypassed. |
| **Expert SQL Overrides (RLHF)** | **Manage & Apply** | **Apply** (Read-Only) | Admins configure custom query overrides; corrected SQL queries trigger automatically for matching questions across all users. |
| **Semantic Configurator Console** | **Read & Write** | **Hidden** | Settings drawer for managing DB metadata, column mapping definitions, and metrics formulas. |
| **GitOps PR Sync Automation** | **Enabled** | **Hidden** | Stages configurations, commits them to the repository, and creates/updates remote branch pull requests. |
| **Immutable Audit Log Ledger** | **Verify & Read** | **Logged** (Background Only) | Every query and dataset result is cryptographically signed and chained in a tamper-evident audit ledger. |
