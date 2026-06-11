import os
import unittest
from sqlalchemy import create_engine
from backend.metadata_rag import MetadataRAG

class TestMetadataRAG(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Establish SQLite connection to existing local ehr_data.db
        BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        db_path = os.path.join(BASE_DIR, "ehr_data.db")
        cls.engine = create_engine(f"sqlite:///{db_path}")
        cls.rag = MetadataRAG(cls.engine)

    def test_medication_query(self):
        # Asking about medications should return 'medications' and 'patients'
        tables = self.rag.retrieve_tables("What medications is the patient taking?", top_k=3)
        self.assertIn("medications", tables)
        self.assertIn("patients", tables)

    def test_department_admissions(self):
        # Asking about department admissions should return 'encounters' and 'departments'
        tables = self.rag.retrieve_tables("Show all admissions in Cardiology department", top_k=3)
        self.assertIn("encounters", tables)
        self.assertIn("departments", tables)

    def test_vitals_query(self):
        # Asking about vitals (sbp, dbp) should return 'vitals'
        tables = self.rag.retrieve_tables("Check vitals heart rate dbp sbp of patients", top_k=3)
        self.assertIn("vitals", tables)
        self.assertIn("patients", tables)

    def test_allergy_query(self):
        # Asking about allergies should return 'allergies'
        tables = self.rag.retrieve_tables("Does patient have any allergies?", top_k=3)
        self.assertIn("allergies", tables)
        self.assertIn("patients", tables)

    def test_tfidf_fallback_graceful_run(self):
        # Temporarily clear embeddings to test local TF-IDF matching and relational expansion fallback paths
        self.rag.embeddings = None
        self.rag.table_embeddings = {}
        tables = self.rag.retrieve_tables("Show patient vitals heart rate sbp", top_k=3)
        self.assertIn("vitals", tables)
        self.assertIn("patients", tables)

if __name__ == "__main__":
    unittest.main()
