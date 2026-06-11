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
