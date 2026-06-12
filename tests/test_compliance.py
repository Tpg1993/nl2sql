import os
import sys
import unittest

# Add parent directory to path so backend can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.agent import EHRQueryAgent
from backend.app import apply_policy_masking, DEMO_USERS
from backend.auth import get_password_hash, verify_password

class TestComplianceGuardrails(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        # Prevent starting full LLM connections, mock agent schema
        cls.agent = EHRQueryAgent()

    def test_pii_sanitization(self):
        """Verifies that explicit PII (emails, phone numbers, SSNs) is extracted and redacted from inputs."""
        email_q = "Show clinical notes for patient john.doe@email.com."
        sanitized, params = self.agent.sanitize_and_extract_pii(email_q)
        self.assertIn("PII_PARAM_0", sanitized)
        self.assertNotIn("john.doe@email.com", sanitized)
        self.assertEqual(params.get("PII_PARAM_0"), "john.doe@email.com")

        phone_q = "Call patient at +1-555-0199 or 555-333-4444"
        sanitized_p, params_p = self.agent.sanitize_and_extract_pii(phone_q)
        self.assertIn("PII_PARAM_0", sanitized_p)
        self.assertIn("PII_PARAM_1", sanitized_p)
        self.assertNotIn("555-333-4444", sanitized_p)

        ssn_q = "What is the diagnosis for patient with SSN 000-12-3456?"
        sanitized_s, params_s = self.agent.sanitize_and_extract_pii(ssn_q)
        self.assertIn("PII_PARAM_0", sanitized_s)
        self.assertNotIn("000-12-3456", sanitized_s)

    def test_demo_user_credentials(self):
        """Verifies that demo credentials exist and verify password hashes correctly."""
        self.assertIn("admin", DEMO_USERS)
        self.assertIn("doctor", DEMO_USERS)
        self.assertIn("researcher", DEMO_USERS)
        
        # Test Admin
        admin_pwd = os.environ.get("ADMIN_PASSWORD", "admin123")
        self.assertTrue(verify_password(admin_pwd, DEMO_USERS["admin"]["password_hash"]))
        self.assertEqual(DEMO_USERS["admin"]["role"], "admin")

        # Test Doctor
        self.assertTrue(verify_password("doctor123", DEMO_USERS["doctor"]["password_hash"]))
        self.assertEqual(DEMO_USERS["doctor"]["role"], "doctor")
        self.assertEqual(DEMO_USERS["doctor"]["attributes"].get("department_id"), 1)

        # Test Researcher
        self.assertTrue(verify_password("researcher123", DEMO_USERS["researcher"]["password_hash"]))
        self.assertEqual(DEMO_USERS["researcher"]["role"], "researcher")

    def test_dynamic_masking_admin(self):
        """Verifies that administrators have unrestricted access to data (no masking)."""
        raw_data = [
            {"full_name": "Alice Green", "dob": "1985-04-12", "phone": "555-123-4567", "email": "alice@ehr.com", "total_charges": 150.0}
        ]
        sql = "SELECT full_name, dob, phone, email, total_charges FROM patients JOIN encounters ON patients.patient_id = encounters.patient_id"
        admin_context = {"username": "admin", "role": "admin", "attributes": {}}
        
        masked = apply_policy_masking(raw_data, sql, admin_context, self.agent)
        self.assertEqual(masked[0]["full_name"], "Alice Green")
        self.assertEqual(masked[0]["total_charges"], 150.0)

    def test_dynamic_masking_researcher(self):
        """Verifies that researchers always have patient identifiers and financial columns masked/redacted."""
        raw_data = [
            {"full_name": "Alice Green", "dob": "1985-04-12", "phone": "555-123-4567", "email": "alice@ehr.com", "total_charges": 150.0}
        ]
        sql = "SELECT full_name, dob, phone, email, total_charges FROM patients JOIN encounters ON patients.patient_id = encounters.patient_id"
        researcher_context = {"username": "researcher", "role": "researcher", "attributes": {}}
        
        masked = apply_policy_masking(raw_data, sql, researcher_context, self.agent)
        # Check PII names and emails are masked
        self.assertIn("*", masked[0]["full_name"])
        self.assertEqual(masked[0]["phone"], "***-***-****")
        self.assertEqual(masked[0]["dob"], "****-**-**")
        self.assertIn("@***.com", masked[0]["email"])
        # Check financial redacted
        self.assertEqual(masked[0]["total_charges"], "[RESTRICTED]")

    def test_dynamic_masking_doctor_abac(self):
        """Verifies that doctors can view patient details in their department, but other patients' PII is masked."""
        raw_data = [
            {"full_name": "Alice Green", "department_id": 1, "total_charges": 150.0},
            {"full_name": "Bob Brown", "department_id": 2, "total_charges": 220.0}
        ]
        sql = "SELECT full_name, department_id, total_charges FROM patients JOIN encounters ON patients.patient_id = encounters.patient_id"
        doctor_context = {"username": "doctor_bob", "role": "doctor", "attributes": {"department_id": 1}}
        
        masked = apply_policy_masking(raw_data, sql, doctor_context, self.agent)
        
        # Patient in Doctor's department (department_id = 1) -> PII Name is visible
        self.assertEqual(masked[0]["full_name"], "Alice Green")
        # Financial is redacted for doctors
        self.assertEqual(masked[0]["total_charges"], "[RESTRICTED]")
        
        # Patient in OTHER department (department_id = 2) -> PII Name is masked
        self.assertIn("*", masked[1]["full_name"])
        self.assertEqual(masked[1]["total_charges"], "[RESTRICTED]")

    def test_ast_safety_valid_complex_queries(self):
        """Verifies that complex, valid read-only SQL queries parse and pass the safety checks successfully."""
        valid_queries = [
            "SELECT p.full_name, COUNT(e.encounter_id) FROM patients p LEFT JOIN encounters e ON p.patient_id = e.patient_id GROUP BY p.full_name",
            "WITH dept_avg AS (SELECT department_id, AVG(total_charges) as avg_chg FROM encounters GROUP BY department_id) SELECT * FROM dept_avg WHERE avg_chg > 100",
            "SELECT full_name, ROW_NUMBER() OVER (PARTITION BY gender ORDER BY dob) FROM patients"
        ]
        for sql in valid_queries:
            try:
                self.agent.audit_sql_query(sql)
            except Exception as e:
                self.fail(f"AST Safety parser rejected a valid read-only query: {sql}. Error: {e}")

    def test_ast_safety_blocked_writes(self):
        """Verifies that mutation commands (insert, update, delete, drop, alter, create) are successfully blocked."""
        unsafe_queries = [
            "DELETE FROM patients WHERE patient_id = 5",
            "DROP TABLE encounters",
            "INSERT INTO vitals (patient_id, sbp) VALUES (1, 120)",
            "UPDATE patients SET full_name = 'Hacked'",
            "ALTER TABLE encounters ADD COLUMN temp TEXT",
            "CREATE TABLE hack (id INTEGER)",
            "SELECT * FROM patients; DROP TABLE patients;",
            "WITH hacked AS (SELECT * FROM patients) DELETE FROM encounters"
        ]
        for sql in unsafe_queries:
            with self.assertRaises(ValueError) as context:
                self.agent.audit_sql_query(sql)
            self.assertIn("Security violation", str(context.exception))

if __name__ == "__main__":
    unittest.main()
