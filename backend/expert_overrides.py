import os
import sqlite3
import hashlib
import time
from typing import Optional

class ExpertOverrideStore:
    """A persistent SQLite store for recording expert modifications to SQL queries.
    If a query has been corrected by an analyst, the agent uses the correction.
    """

    def __init__(self, db_path: str = None) -> None:
        if db_path is None:
            BASE_DIR = os.path.dirname(os.path.abspath(__file__))
            db_path = os.path.join(BASE_DIR, "cache.db")  # Re-use cache.db file
        
        self.db_path = db_path
        # Persistent connection to allow in-memory SQLite testing and avoid locks
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._init_db()

    def _init_db(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS expert_overrides (
                question_hash TEXT PRIMARY KEY,
                question TEXT,
                corrected_sql TEXT,
                created_at REAL
            )
        """)
        self.conn.commit()

    def get_override(self, question: str) -> Optional[str]:
        """Check if an analyst has corrected the SQL for this specific question."""
        q_hash = hashlib.sha256(question.lower().strip().encode("utf-8")).hexdigest()
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT corrected_sql FROM expert_overrides WHERE question_hash = ?",
                (q_hash,)
            )
            row = cursor.fetchone()
            if row:
                print(f"\n[Expert Override Hit] Found expert-approved SQL query for: '{question.strip()}'")
                return row[0]
        except Exception as e:
            print(f"Error reading from expert override store: {e}")
        return None

    def set_override(self, question: str, corrected_sql: str) -> None:
        """Saves/updates an analyst's corrected SQL query for a question."""
        q_hash = hashlib.sha256(question.lower().strip().encode("utf-8")).hexdigest()
        try:
            self.conn.execute(
                "INSERT OR REPLACE INTO expert_overrides (question_hash, question, corrected_sql, created_at) VALUES (?, ?, ?, ?)",
                (
                    q_hash,
                    question.strip(),
                    corrected_sql.strip(),
                    time.time()
                )
            )
            self.conn.commit()
            print(f"[Expert Override Store] Saved corrected SQL for: '{question.strip()}'")
        except Exception as e:
            print(f"Error saving to expert override store: {e}")
