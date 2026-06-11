import os
import unittest
import sqlite3
import tempfile
import json
from datetime import datetime, timezone

from backend.audit_ledger import AuditLedger

class TestAuditLedger(unittest.TestCase):
    def setUp(self):
        # Create a temporary database file for isolation
        self.db_fd, self.db_path = tempfile.mkstemp()
        self.ledger = AuditLedger(db_path=self.db_path)

    def tearDown(self):
        # Close and remove the temporary DB file
        os.close(self.db_fd)
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_sqlglot_parsing(self):
        """Verifies sqlglot extracts affected tables and columns correctly."""
        sql = "SELECT p.full_name, COUNT(e.encounter_id) FROM patients p LEFT JOIN encounters e ON p.patient_id = e.patient_id GROUP BY p.full_name"
        res = AuditLedger.get_affected_tables_and_columns(sql)
        
        self.assertIn("patients", res["tables"])
        self.assertIn("encounters", res["tables"])
        self.assertIn("full_name", res["columns"])
        self.assertIn("encounter_id", res["columns"])
        self.assertIn("patient_id", res["columns"])

    def test_write_and_chaining(self):
        """Checks that inserting logs correctly builds the cryptographic SHA-256 chain."""
        # 1. First insert
        log1 = self.ledger.write_audit_log(
            username="admin",
            role="admin",
            prompt="How many patients?",
            sql_query="SELECT COUNT(*) FROM patients",
            latency_ms=12.5,
            dataset=[{"count": 50}]
        )
        
        self.assertEqual(log1["previous_hash"], "0")
        self.assertIsNotNone(log1["record_hash"])

        # 2. Second insert
        log2 = self.ledger.write_audit_log(
            username="researcher",
            role="researcher",
            prompt="Get vitals",
            sql_query="SELECT sbp FROM vitals LIMIT 5",
            latency_ms=8.2,
            dataset=[[120], [118]]
        )
        
        self.assertEqual(log2["previous_hash"], log1["record_hash"])
        
        # Verify integrity is intact
        verified, tampered = self.ledger.verify_ledger_integrity()
        self.assertTrue(verified)
        self.assertEqual(len(tampered), 0)

    def test_tampering_detection(self):
        """Simulates direct database tampering and checks that verification catches it."""
        # Insert 3 records
        self.ledger.write_audit_log("admin", "admin", "Q1", "SELECT * FROM patients", 10.0, [{"id": 1}])
        self.ledger.write_audit_log("admin", "admin", "Q2", "SELECT * FROM vitals", 15.0, [{"id": 2}])
        self.ledger.write_audit_log("admin", "admin", "Q3", "SELECT * FROM encounters", 20.0, [{"id": 3}])
        
        # Ensure it passes originally
        verified, tampered = self.ledger.verify_ledger_integrity()
        self.assertTrue(verified)
        
        # Directly update the prompt of row 2 in the database to simulate tampering
        conn = sqlite3.connect(self.ledger.db_path)
        try:
            conn.execute("UPDATE audit_logs SET prompt = 'TAMPERED' WHERE id = 2")
            conn.commit()
        finally:
            conn.close()
        
        # Verify it now fails and reports row 2 as tampered (due to broken chain)
        verified, tampered = self.ledger.verify_ledger_integrity()
        self.assertFalse(verified)
        self.assertIn(2, tampered)

if __name__ == "__main__":
    unittest.main()
