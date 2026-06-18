import os
import sqlite3
import hashlib
import time
from typing import Optional

class ExpertOverrideStore:
    """A persistent SQLite store for recording expert modifications to SQL queries.
    If a query has been corrected by an analyst, the agent uses the correction.
    Now supports user-specific overrides with global fallbacks.
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
        # Check if table already has username column, otherwise drop it to migrate
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT username FROM expert_overrides LIMIT 1")
        except sqlite3.OperationalError:
            print("[Expert Override Store] Migrating database: dropping old expert_overrides table...")
            try:
                self.conn.execute("DROP TABLE IF EXISTS expert_overrides")
                self.conn.commit()
            except Exception as e:
                print(f"[Expert Override Store] Warning: failed to drop old table: {e}")

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS expert_overrides (
                question_hash TEXT,
                username TEXT DEFAULT 'global',
                question TEXT,
                corrected_sql TEXT,
                created_at REAL,
                PRIMARY KEY (question_hash, username)
            )
        """)
        self.conn.commit()

    def get_override(self, question: str, username: str = None) -> Optional[str]:
        """Check if an analyst has corrected the SQL for this specific question (user-specific or global)."""
        q_hash = hashlib.sha256(question.lower().strip().encode("utf-8")).hexdigest()
        try:
            cursor = self.conn.cursor()
            
            # 1. Try to find user-specific override first
            if username:
                cursor.execute(
                    "SELECT corrected_sql FROM expert_overrides WHERE question_hash = ? AND username = ?",
                    (q_hash, username)
                )
                row = cursor.fetchone()
                if row:
                    print(f"\n[Expert Override Hit] Found expert-approved SQL query for user '{username}': '{question.strip()}'")
                    return row[0]
            
            # 2. Fall back to global override
            cursor.execute(
                "SELECT corrected_sql FROM expert_overrides WHERE question_hash = ? AND username = 'global'",
                (q_hash,)
            )
            row = cursor.fetchone()
            if row:
                print(f"\n[Expert Override Hit] Found global expert-approved SQL query for: '{question.strip()}'")
                return row[0]
                
        except Exception as e:
            print(f"Error reading from expert override store: {e}")
        return None

    def set_override(self, question: str, corrected_sql: str, username: str = None) -> None:
        """Saves/updates an analyst's corrected SQL query for a question (user-specific or global)."""
        q_hash = hashlib.sha256(question.lower().strip().encode("utf-8")).hexdigest()
        target_user = username if username else "global"
        try:
            self.conn.execute(
                "INSERT OR REPLACE INTO expert_overrides (question_hash, username, question, corrected_sql, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    q_hash,
                    target_user,
                    question.strip(),
                    corrected_sql.strip(),
                    time.time()
                )
            )
            self.conn.commit()
            print(f"[Expert Override Store] Saved corrected SQL for user '{target_user}': '{question.strip()}'")
        except Exception as e:
            print(f"Error saving to expert override store: {e}")
