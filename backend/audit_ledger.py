import os
import sqlite3
import json
import hashlib
from datetime import datetime, timezone
import sqlglot
import sqlglot.expressions as exp
from typing import Dict, List, Tuple, Any

class AuditLedger:
    def __init__(self, db_path: str = None):
        """Initializes the Audit Ledger and ensures the database table exists."""
        if db_path is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            db_path = os.path.join(base_dir, "audit_ledger.db")
        self.db_path = db_path
        self._ensure_table_exists()

    def _get_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _ensure_table_exists(self):
        conn = self._get_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    username TEXT NOT NULL,
                    role TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    sql_query TEXT NOT NULL,
                    affected_tables TEXT NOT NULL,
                    affected_columns TEXT NOT NULL,
                    latency_ms REAL NOT NULL,
                    dataset_hash TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    record_hash TEXT NOT NULL
                )
            """)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def get_affected_tables_and_columns(sql_query: str, dialect: str = "sqlite") -> Dict[str, List[str]]:
        """Parses a SQL query using sqlglot to extract tables and columns referenced."""
        if not sql_query or not sql_query.strip():
            return {"tables": [], "columns": []}

        try:
            parsed_expressions = sqlglot.parse(sql_query, read=dialect)
        except Exception:
            try:
                parsed_expressions = sqlglot.parse(sql_query)
            except Exception:
                return {"tables": [], "columns": []}

        tables = set()
        columns = set()

        for expr in parsed_expressions:
            if not expr:
                continue
            for node in expr.walk():
                if isinstance(node, exp.Table):
                    tbl_name = node.name.lower()
                    if tbl_name:
                        tables.add(tbl_name)
                elif isinstance(node, exp.Column):
                    col_name = node.name.lower()
                    if col_name:
                        columns.add(col_name)

        return {
            "tables": sorted(list(tables)),
            "columns": sorted(list(columns))
        }

    @staticmethod
    def calculate_dataset_hash(dataset: Any) -> str:
        """Calculates a deterministic SHA-256 hash representing the dataset."""
        dataset_json = json.dumps(dataset, sort_keys=True, default=str)
        return hashlib.sha256(dataset_json.encode("utf-8")).hexdigest()

    def _get_last_record_hash(self) -> str:
        """Retrieves the record_hash of the latest record, or '0' if empty."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT record_hash FROM audit_logs ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            return row[0] if row else "0"
        finally:
            conn.close()

    def write_audit_log(
        self,
        username: str,
        role: str,
        prompt: str,
        sql_query: str,
        latency_ms: float,
        dataset: Any
    ) -> Dict[str, Any]:
        """Appends a new audit log entry cryptographically chained to the previous entry."""
        timestamp = datetime.now(timezone.utc).isoformat()
        
        # 1. Extract affected tables and columns
        metadata = self.get_affected_tables_and_columns(sql_query)
        tables_json = json.dumps(metadata["tables"])
        columns_json = json.dumps(metadata["columns"])
        
        # 2. Compute dataset hash
        dataset_hash = self.calculate_dataset_hash(dataset)
        
        # 3. Fetch previous hash
        previous_hash = self._get_last_record_hash()
        
        # 4. Compute record hash representing structural non-repudiation
        payload_str = (
            f"{timestamp}|{username}|{role}|{prompt}|{sql_query}|"
            f"{tables_json}|{columns_json}|{latency_ms}|{dataset_hash}|{previous_hash}"
        )
        record_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
        
        # 5. Insert record
        conn = self._get_connection()
        try:
            conn.execute(
                """
                INSERT INTO audit_logs (
                    timestamp, username, role, prompt, sql_query,
                    affected_tables, affected_columns, latency_ms,
                    dataset_hash, previous_hash, record_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp, username, role, prompt, sql_query,
                    tables_json, columns_json, latency_ms,
                    dataset_hash, previous_hash, record_hash
                )
            )
            conn.commit()
        finally:
            conn.close()
            
        return {
            "timestamp": timestamp,
            "username": username,
            "role": role,
            "prompt": prompt,
            "sql_query": sql_query,
            "affected_tables": metadata["tables"],
            "affected_columns": metadata["columns"],
            "latency_ms": latency_ms,
            "dataset_hash": dataset_hash,
            "previous_hash": previous_hash,
            "record_hash": record_hash
        }

    def verify_ledger_integrity(self) -> Tuple[bool, List[int]]:
        """
        Validates the entire hash chain. Returns (True, []) if verified successfully,
        or (False, tampered_row_ids) if tampering is detected.
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, timestamp, username, role, prompt, sql_query,
                       affected_tables, affected_columns, latency_ms,
                       dataset_hash, previous_hash, record_hash
                FROM audit_logs ORDER BY id ASC
            """)
            rows = cursor.fetchall()
        finally:
            conn.close()

        expected_prev_hash = "0"
        tampered_ids = []

        for row in rows:
            row_id, timestamp, username, role, prompt, sql_query, tables_json, columns_json, latency_ms, dataset_hash, previous_hash, record_hash = row
            
            is_row_valid = True
            
            # Verify chain linkage
            if previous_hash != expected_prev_hash:
                is_row_valid = False
                
            # Verify record structural hash
            payload_str = (
                f"{timestamp}|{username}|{role}|{prompt}|{sql_query}|"
                f"{tables_json}|{columns_json}|{latency_ms}|{dataset_hash}|{previous_hash}"
            )
            calculated_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
            
            if record_hash != calculated_hash:
                is_row_valid = False
                
            if not is_row_valid:
                tampered_ids.append(row_id)
                
            # Chain continues: we expect the DB-recorded record_hash as previous_hash of the next row
            expected_prev_hash = record_hash

        return (len(tampered_ids) == 0, tampered_ids)

    def get_all_logs(self) -> List[Dict[str, Any]]:
        """Returns all audit logs in descending chronological order."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, timestamp, username, role, prompt, sql_query,
                       affected_tables, affected_columns, latency_ms,
                       dataset_hash, previous_hash, record_hash
                FROM audit_logs ORDER BY id DESC
            """)
            rows = cursor.fetchall()
        finally:
            conn.close()

        logs = []
        for row in rows:
            row_id, timestamp, username, role, prompt, sql_query, tables_json, columns_json, latency_ms, dataset_hash, previous_hash, record_hash = row
            logs.append({
                "id": row_id,
                "timestamp": timestamp,
                "username": username,
                "role": role,
                "prompt": prompt,
                "sql_query": sql_query,
                "affected_tables": json.loads(tables_json),
                "affected_columns": json.loads(columns_json),
                "latency_ms": latency_ms,
                "dataset_hash": dataset_hash,
                "previous_hash": previous_hash,
                "record_hash": record_hash
            })
        return logs
