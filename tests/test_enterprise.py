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
        # Set mock table sizes to trigger high cost block (> 5000 rows)
        self.cost_planner.table_sizes = {
            "encounters": 6000,
            "providers": 3000
        }
        # Query scanning tables without index triggers full scans totaling > 5000 rows
        sql = "SELECT * FROM encounters e JOIN providers pr ON e.patient_id = pr.provider_id"
        is_safe, reason, metrics = self.cost_planner.analyze_query(sql)
        self.assertFalse(is_safe)
        self.assertIn("high execution cost", reason.lower())
        self.assertIn("estimated 6001 rows scanned", reason.lower())

    def test_cost_planner_weighted_rows_estimation(self):
        # Test that using an index is estimated cheaper than a full scan
        self.cost_planner.table_sizes = {"patients": 10000}
        
        # 1. Full scan query on patients
        sql_full = "SELECT * FROM patients WHERE is_active = 1"
        _, _, metrics_full = self.cost_planner.analyze_query(sql_full)
        self.assertEqual(metrics_full.get("total_estimated_rows_scanned"), 10000)
        
        # 2. Query with index (primary key lookup)
        sql_index = "SELECT * FROM patients WHERE patient_id = 5"
        _, _, metrics_index = self.cost_planner.analyze_query(sql_index)
        # Primary key lookup is a SEARCH, which maps to 1 row scan in SQLite explain plan
        self.assertEqual(metrics_index.get("total_estimated_rows_scanned"), 1)

    def test_cost_planner_missing_index_advisories(self):
        # Set substantial size for tables so advisories are generated (> 100 rows)
        self.cost_planner.table_sizes = {"encounters": 200}
        
        # Query filtering on unindexed column encounters.patient_id
        sql = "SELECT * FROM encounters WHERE patient_id = 123"
        is_safe, reason, metrics = self.cost_planner.analyze_query(sql)
        
        advisories = metrics.get("optimizer_advisories", [])
        self.assertTrue(len(advisories) > 0)
        self.assertEqual(advisories[0]["table"], "encounters")
        self.assertEqual(advisories[0]["column"], "patient_id")
        self.assertIn("CREATE INDEX idx_encounters_patient_id ON encounters(patient_id);", advisories[0]["suggestion"])

    def test_cost_planner_role_bypass(self):
        from backend.agent import EHRQueryAgent
        from langchain_core.messages import HumanMessage
        import sqlite3
        
        # Initialize agent with a temporary SQLite file in the tests/results/ directory
        results_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "results"))
        os.makedirs(results_dir, exist_ok=True)
        db_file = os.path.join(results_dir, "temp_role_bypass_test.db")
        db_uri = f"sqlite:///{db_file}"
        
        # Ensure clean state and initialize the file with the table first
        if os.path.exists(db_file):
            try:
                os.remove(db_file)
            except Exception:
                pass
                
        try:
            conn = sqlite3.connect(db_file)
            conn.execute("CREATE TABLE encounters (encounter_id INTEGER, patient_id INTEGER)")
            conn.commit()
            conn.close()
            
            agent = EHRQueryAgent(db_uri=db_uri)
            
            # Mock planner sizes to trigger violation (> 5000 rows)
            agent.cost_planner.table_sizes = {"encounters": 6000}
            
            # Configure HighPerformanceQueryGroup in security policies
            agent.semantic_layer.security_policies["high_performance_groups"] = {
                "HighPerformanceQueryGroup": ["doctor"]
            }
            
            sql = "SELECT * FROM encounters"
            
            # Case 1: Researcher role (not in HighPerformanceQueryGroup) -> should trigger cost violation
            state_researcher = {
                "messages": [HumanMessage(content=sql)],
                "retries": 0,
                "latencies": {},
                "user_context": {"role": "researcher"},
                "is_expert_matched": False
            }
            res_researcher = agent.execute_query_node(state_researcher)
            self.assertIn("Cost violation", res_researcher["messages"][-1].content)
            
            # Case 2: Doctor role (in HighPerformanceQueryGroup) -> should bypass cost violation and run
            state_doctor = {
                "messages": [HumanMessage(content=sql)],
                "retries": 0,
                "latencies": {},
                "user_context": {"role": "doctor"},
                "is_expert_matched": False
            }
            res_doctor = agent.execute_query_node(state_doctor)
            self.assertNotIn("Cost violation", res_doctor["messages"][-1].content)
            
        finally:
            if os.path.exists(db_file):
                try:
                    os.remove(db_file)
                except Exception:
                    pass

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
