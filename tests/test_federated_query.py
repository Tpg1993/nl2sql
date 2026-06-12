import os
import sys
import unittest

# Add parent directory to path so backend can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.agent import EHRQueryAgent

class TestFederatedQueryRouter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Initialize the EHRQueryAgent using the fallback database URI
        cls.agent = EHRQueryAgent()

    def test_is_federated_query(self):
        # A query involving patients (local) and departments (remote) is federated
        q1 = "SELECT p.full_name, d.department_name FROM patients p JOIN encounters e ON p.patient_id = e.patient_id JOIN departments d ON e.department_id = d.department_id"
        self.assertTrue(self.agent.is_federated_query(q1))

        # A query involving only local tables is NOT federated
        q2 = "SELECT * FROM patients p JOIN vitals v ON p.patient_id = v.patient_id"
        self.assertFalse(self.agent.is_federated_query(q2))

        # A query involving only remote tables is NOT federated
        q3 = "SELECT * FROM encounters e JOIN departments d ON e.department_id = d.department_id"
        self.assertFalse(self.agent.is_federated_query(q3))

    def test_decompose_query(self):
        sql = (
            "SELECT p.full_name, d.department_name, e.total_charges "
            "FROM patients p "
            "JOIN encounters e ON p.patient_id = e.patient_id "
            "JOIN departments d ON e.department_id = d.department_id "
            "ORDER BY e.total_charges DESC LIMIT 5"
        )
        local_q, remote_q, joins, parsed = self.agent.decompose_query(sql)

        # Assert local query references patients
        self.assertIn("patients", local_q.lower())
        self.assertNotIn("departments", local_q.lower())
        
        # Assert remote query references encounters and departments
        self.assertIn("encounters", remote_q.lower())
        self.assertIn("departments", remote_q.lower())
        self.assertNotIn("patients", remote_q.lower())

        # Assert join keys are extracted correctly
        self.assertEqual(len(joins), 1)
        self.assertEqual(joins[0][0], "p") # local alias
        self.assertEqual(joins[0][1], "patient_id") # local key
        self.assertEqual(joins[0][2], "e") # remote alias
        self.assertEqual(joins[0][3], "patient_id") # remote key

    def test_execute_federated_query(self):
        # Test full E2E virtualization split-join execution logic
        sql = (
            "SELECT p.full_name, d.department_name, e.total_charges "
            "FROM patients p "
            "JOIN encounters e ON p.patient_id = e.patient_id "
            "JOIN departments d ON e.department_id = d.department_id "
            "ORDER BY e.total_charges DESC LIMIT 5"
        )
        
        # Execute query
        res_str = self.agent.execute_federated_query(sql)
        import ast
        res_list = ast.literal_eval(res_str)

        # Assert correct format and tuple counts
        self.assertTrue(isinstance(res_list, list))
        self.assertEqual(len(res_list), 5)
        
        # Check that the first tuple matches the highest charge patient we saw in scratch tests
        first_row = res_list[0]
        self.assertEqual(len(first_row), 3) # (full_name, department_name, total_charges)
        self.assertEqual(first_row[0], "Saanvi Das")
        self.assertEqual(first_row[1], "Diabetology")

if __name__ == "__main__":
    unittest.main()
