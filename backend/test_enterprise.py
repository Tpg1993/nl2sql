import unittest
import os
import sys
from sqlalchemy import create_engine, text

# Add parent directory to path so backend can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.semantic_layer import SemanticLayer
from backend.planner import CostPlanner
from backend.expert_overrides import ExpertOverrideStore
from backend.prompts import PromptLibrary

class TestEnterpriseNL2SQL(unittest.TestCase):

    def setUp(self):
        # Initialize in-memory SQLite database for test plans
        self.engine = create_engine("sqlite://")
        with self.engine.connect() as conn:
            conn.execute(text("CREATE TABLE patients (patient_id INTEGER PRIMARY KEY, full_name TEXT, is_active INTEGER)"))
            conn.execute(text("CREATE TABLE encounters (encounter_id INTEGER, patient_id INTEGER, total_charges REAL)"))
            conn.execute(text("CREATE TABLE providers (provider_id INTEGER, department_id INTEGER)"))
            conn.execute(text("CREATE TABLE departments (department_id INTEGER, is_active INTEGER)"))
            conn.commit()

        self.semantic_layer = SemanticLayer()
        self.cost_planner = CostPlanner(self.engine)
        
        # Initialize overrides store using an in-memory DB
        self.override_store = ExpertOverrideStore(db_path=":memory:")

    def tearDown(self):
        pass

    def test_semantic_layer_load(self):
        self.assertIsNotNone(self.semantic_layer.config)
        self.assertEqual(self.semantic_layer.get_table_name("Patient"), "patients")
        self.assertEqual(self.semantic_layer.get_field_mapping("Patient", "name"), "full_name")
        self.assertIn("SEMANTIC LAYER DEFINITIONS", self.semantic_layer.get_context_prompt())

    def test_cost_planner_safe_query(self):
        sql = "SELECT p.full_name FROM patients p JOIN encounters e ON p.patient_id = e.patient_id LIMIT 10"
        is_safe, reason, metrics = self.cost_planner.analyze_query(sql)
        self.assertTrue(is_safe)
        self.assertEqual(metrics.get("scanned_tables_count"), 1)

    def test_cost_planner_unsafe_query_too_many_scans(self):
        # Query scanning 4 tables without indexes
        sql = "SELECT * FROM patients p JOIN encounters e JOIN providers pr JOIN departments d"
        is_safe, reason, metrics = self.cost_planner.analyze_query(sql)
        self.assertFalse(is_safe)
        self.assertIn("too many full table scans", reason.lower())

    def test_cost_planner_cartesian_product(self):
        # Cartesian join
        sql = "SELECT * FROM patients, encounters"
        is_safe, reason, metrics = self.cost_planner.analyze_query(sql)
        self.assertFalse(is_safe)
        self.assertIn("cartesian product", reason.lower())

    def test_expert_overrides(self):
        question = "Give me patient summary counts"
        sql = "SELECT COUNT(*) FROM patients"
        self.override_store.set_override(question, sql)
        
        override = self.override_store.get_override(question)
        self.assertEqual(override, sql)
        
        # Test case-insensitivity and whitespace trim
        override_variant = self.override_store.get_override("  Give me patient summary counts  ")
        self.assertEqual(override_variant, sql)

    def test_prompt_library_loading(self):
        library = PromptLibrary()
        self.assertIn("sql_generation.txt", library.templates)
        self.assertIn("summarization.txt", library.templates)
        
        # Test formatting
        formatted_sql = library.format_sql_generation(
            user_question="How many patients?",
            semantic_context="Logical Entities",
            db_schema="CREATE TABLE patients"
        )
        self.assertIn("How many patients?", formatted_sql)
        self.assertIn("Logical Entities", formatted_sql)
        self.assertIn("CREATE TABLE patients", formatted_sql)
        
        # Test retry format injection
        formatted_retry = library.format_sql_generation(
            user_question="How many patients?",
            semantic_context="Logical Entities",
            db_schema="CREATE TABLE patients",
            previous_error="Syntax error near select"
        )
        self.assertIn("Syntax error near select", formatted_retry)

if __name__ == "__main__":
    unittest.main()
