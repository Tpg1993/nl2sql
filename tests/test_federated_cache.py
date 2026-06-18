import os
import sys
import time
import unittest
import json
import sqlite3
import hashlib

# Add parent directory to path so backend can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.agent import EHRQueryAgent
from backend.cache import SQLiteCacheManager


class TestFederatedQueryCaching(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Setup temporary cache db path
        BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        cls.test_cache_db = os.path.join(BASE_DIR, "test_cache.db")
        if os.path.exists(cls.test_cache_db):
            os.remove(cls.test_cache_db)

        # Initialize cache manager and agent
        cls.cache_manager = SQLiteCacheManager(cache_db_path=cls.test_cache_db, ttl_seconds=5)
        cls.agent = EHRQueryAgent(cache_manager=cls.cache_manager)

    @classmethod
    def tearDownClass(cls):
        # Remove temporary cache db
        try:
            if os.path.exists(cls.test_cache_db):
                os.remove(cls.test_cache_db)
        except Exception:
            pass

    def setUp(self):
        self.cache_manager.clear()

    def test_sql_cache_set_and_get(self):
        sql = "SELECT * FROM encounters WHERE total_charges > 5000"
        dummy_result = [
            {"encounter_id": 1, "total_charges": 6000.0, "patient_id": 10},
            {"encounter_id": 2, "total_charges": 5500.0, "patient_id": 12}
        ]

        # Ensure cache starts empty
        self.assertIsNone(self.cache_manager.get_sql(sql))

        # Store in cache
        self.cache_manager.set_sql(sql, dummy_result)

        # Retrieve and verify
        cached_val = self.cache_manager.get_sql(sql)
        self.assertEqual(cached_val, dummy_result)

        # Verify clear removes it
        self.cache_manager.clear()
        self.assertIsNone(self.cache_manager.get_sql(sql))

    def test_sql_cache_expiration(self):
        # Create a cache manager with 1 second TTL
        temp_cache_db = self.test_cache_db + "_temp"
        if os.path.exists(temp_cache_db):
            os.remove(temp_cache_db)

        short_cache = SQLiteCacheManager(cache_db_path=temp_cache_db, ttl_seconds=1)
        try:
            sql = "SELECT 1"
            res = [{"test": 1}]
            short_cache.set_sql(sql, res)
            
            # Hit check immediately
            self.assertEqual(short_cache.get_sql(sql), res)

            # Wait for expiration
            time.sleep(1.5)

            # Hit check should now return None
            self.assertIsNone(short_cache.get_sql(sql))
        finally:
            try:
                if os.path.exists(temp_cache_db):
                    os.remove(temp_cache_db)
            except Exception:
                pass

    def test_federated_sub_query_caching_e2e(self):
        # A standard federated query
        sql = (
            "SELECT p.full_name, d.department_name, e.total_charges "
            "FROM patients p "
            "JOIN encounters e ON p.patient_id = e.patient_id "
            "JOIN departments d ON e.department_id = d.department_id "
            "ORDER BY e.total_charges DESC LIMIT 5"
        )

        # 1. Run first time (should store in cache)
        res_str = self.agent.execute_federated_query(sql)
        import ast
        res_list = ast.literal_eval(res_str)
        self.assertTrue(len(res_list) > 0)

        # 2. Verify sub-query exists in the cache table
        local_q, remote_q, joins, parsed = self.agent.decompose_query(sql)
        self.assertTrue(bool(self.agent.last_executed_remote_query))
        
        # Check cache directly using remote query string
        cached_remote_rows = self.cache_manager.get_sql(self.agent.last_executed_remote_query)
        self.assertIsNotNone(cached_remote_rows)
        self.assertTrue(len(cached_remote_rows) > 0)

        # 3. Modify the cache data on-disk to prove it gets read on the next execution
        modified_rows = []
        for row in cached_remote_rows:
            new_row = row.copy()
            if "department_name" in new_row:
                new_row["department_name"] = "Mocked Cardiology"
            modified_rows.append(new_row)

        self.cache_manager.set_sql(self.agent.last_executed_remote_query, modified_rows)

        # 4. Re-run federated query (should hit cache and return the modified department name)
        second_res_str = self.agent.execute_federated_query(sql)
        second_res_list = ast.literal_eval(second_res_str)

        # Check if the department name in the joined results has the mocked value
        for row in second_res_list:
            # Row index 1 is department_name in the SELECT list: p.full_name, d.department_name, e.total_charges
            self.assertEqual(row[1], "Mocked Cardiology")


if __name__ == "__main__":
    unittest.main()
