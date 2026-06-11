import os
import tempfile
import yaml
import pytest
from backend.few_shot_library import FewShotLibrary

TEST_SEMANTIC_LAYER_CONTENT = """
version: 1.0.0
entities:
  Patient:
    table_name: patients
    primary_key: patient_id
few_shot_examples:
  - question: "Find all patients who have active allergies"
    sql: "SELECT p.patient_id, p.full_name FROM patients p JOIN allergies a ON p.patient_id = a.patient_id WHERE a.is_active = 1;"
  - question: "Show vitals for patients with a BMI greater than 30"
    sql: "SELECT v.patient_id, v.sbp, v.dbp, v.bmi FROM vitals v WHERE v.bmi > 30;"
  - question: "What is the total billing amount for departments in Boston?"
    sql: "SELECT SUM(e.total_charges) FROM encounters e JOIN departments d ON e.department_id = d.department_id WHERE d.location = 'Boston';"
"""

def test_few_shot_library_loading():
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w", encoding="utf-8") as f:
        f.write(TEST_SEMANTIC_LAYER_CONTENT)
        temp_path = f.name

    try:
        library = FewShotLibrary(semantic_layer_path=temp_path)
        assert len(library.examples) == 3
        assert library.examples[0]["question"] == "Find all patients who have active allergies"
        assert library.examples[1]["question"] == "Show vitals for patients with a BMI greater than 30"
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

def test_few_shot_tfidf_matching():
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w", encoding="utf-8") as f:
        f.write(TEST_SEMANTIC_LAYER_CONTENT)
        temp_path = f.name

    try:
        # Force local TF-IDF matcher by clearing API key environment variable in the instance context
        library = FewShotLibrary(semantic_layer_path=temp_path)
        library.embeddings = None
        library.example_embeddings = []

        # Test exact/close matching
        results = library.retrieve_few_shots("Show me patients with active allergies", top_k=1)
        assert len(results) == 1
        assert "allergies" in results[0]["question"]
        assert results[0]["score"] > 0.0

        # Test second match
        results_vitals = library.retrieve_few_shots("What patients have a BMI over 30?", top_k=1)
        assert len(results_vitals) == 1
        assert "vitals" in results_vitals[0]["question"]
        assert "bmi" in results_vitals[0]["question"].lower()
        assert results_vitals[0]["score"] > 0.0

        # Test top_k parameter
        results_multiple = library.retrieve_few_shots("Which patient has active allergies?", top_k=2)
        assert len(results_multiple) == 2
        # The allergy example should have a higher score than the others
        assert results_multiple[0]["question"] == "Find all patients who have active allergies"
        assert results_multiple[0]["score"] > results_multiple[1]["score"]
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

def test_empty_few_shot_library():
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w", encoding="utf-8") as f:
        f.write("version: 1.0.0\n")  # No few_shot_examples key
        temp_path = f.name

    try:
        library = FewShotLibrary(semantic_layer_path=temp_path)
        assert len(library.examples) == 0
        
        # Should return empty list gracefully
        results = library.retrieve_few_shots("Is there any billing?", top_k=2)
        assert results == []
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
