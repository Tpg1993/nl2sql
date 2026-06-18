import os
import sys
import sqlite3
import unittest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app import app
from backend.auth import create_access_token

class TestQueryFeedbackAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["TESTING"] = "true"
        cls.client = TestClient(app)
        
        # Access Tokens
        admin_token = create_access_token(data={"sub": "admin", "role": "admin", "attributes": {}})
        cls.admin_headers = {"Authorization": f"Bearer {admin_token}"}
        
        # Prepare dynamic db path to check outputs
        cls.db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend", "cache.db")
        
        # Delete prior query_feedback table if exists to ensure isolated run
        if os.path.exists(cls.db_path):
            try:
                conn = sqlite3.connect(cls.db_path)
                conn.execute("DROP TABLE IF EXISTS query_feedback")
                conn.commit()
                conn.close()
            except Exception:
                pass

    def test_submit_feedback_unauthorized(self):
        """Verify that requests without access tokens fail with 401."""
        response = self.client.post("/api/feedback", json={
            "thread_id": "test_thread_unauth",
            "question": "What is the count of patients?",
            "sql_query": "SELECT COUNT(*) FROM patients",
            "rating": 1,
            "comment": None
        })
        self.assertEqual(response.status_code, 401)

    def test_submit_feedback_success(self):
        """Verify that valid feedback is successfully logged to SQLite cache.db."""
        feedback_payload = {
            "thread_id": "test_thread_feedback_success",
            "question": "Show vital records for patient 10",
            "sql_query": "SELECT * FROM vitals WHERE patient_id = 10",
            "rating": -1,
            "comment": "Output did not list systolic blood pressure alias correctly"
        }
        
        response = self.client.post(
            "/api/feedback",
            json=feedback_payload,
            headers=self.admin_headers
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("message"), "Feedback submitted successfully.")
        
        # Assert database state
        self.assertTrue(os.path.exists(self.db_path))
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT thread_id, username, question, sql_query, rating, comment FROM query_feedback")
        row = cursor.fetchone()
        conn.close()
        
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "test_thread_feedback_success")
        self.assertEqual(row[1], "admin")
        self.assertEqual(row[2], "Show vital records for patient 10")
        self.assertEqual(row[3], "SELECT * FROM vitals WHERE patient_id = 10")
        self.assertEqual(row[4], -1)
        self.assertEqual(row[5], "Output did not list systolic blood pressure alias correctly")

if __name__ == "__main__":
    unittest.main()
