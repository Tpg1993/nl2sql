import os
import yaml
import unittest
from fastapi.testclient import TestClient
from backend.app import app, DEMO_USERS
from backend.auth import create_access_token

class TestConfigEditorAPI(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        # Generate authorization headers for different roles
        admin_token = create_access_token(data={"sub": "admin", "role": "admin", "attributes": {}})
        doctor_token = create_access_token(data={"sub": "doctor", "role": "doctor", "attributes": {"department_id": 1}})
        researcher_token = create_access_token(data={"sub": "researcher", "role": "researcher", "attributes": {}})
        
        cls.admin_headers = {"Authorization": f"Bearer {admin_token}"}
        cls.doctor_headers = {"Authorization": f"Bearer {doctor_token}"}
        cls.researcher_headers = {"Authorization": f"Bearer {researcher_token}"}
        
        # Backup the current semantic_layer.yaml to restore after tests
        base_dir = os.path.dirname(os.path.abspath(__file__))
        cls.yaml_path = os.path.join(base_dir, "semantic_layer.yaml")
        cls.backup_path = os.path.join(base_dir, "semantic_layer.yaml.backup")
        if os.path.exists(cls.yaml_path):
            with open(cls.yaml_path, "r", encoding="utf-8") as f:
                cls.original_content = f.read()

    @classmethod
    def tearDownClass(cls):
        # Restore the original semantic_layer.yaml
        if hasattr(cls, "original_content"):
            with open(cls.yaml_path, "w", encoding="utf-8") as f:
                f.write(cls.original_content)

    def test_get_config_unauthorized(self):
        """Unauthenticated requests must fail with 401."""
        response = self.client.get("/api/config/semantic-layer")
        self.assertEqual(response.status_code, 401)

    def test_get_config_forbidden_role(self):
        """Non-admin roles must fail with 403."""
        response = self.client.get("/api/config/semantic-layer", headers=self.doctor_headers)
        self.assertEqual(response.status_code, 403)
        
        response = self.client.get("/api/config/semantic-layer", headers=self.researcher_headers)
        self.assertEqual(response.status_code, 403)

    def test_get_config_success(self):
        """Admin role must successfully fetch configuration and database metadata."""
        response = self.client.get("/api/config/semantic-layer", headers=self.admin_headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertIn("config", data)
        self.assertIn("db_metadata", data)
        self.assertIn("patients", data["db_metadata"])

    def test_post_config_forbidden_role(self):
        """Non-admin roles must fail to update configurations with 403."""
        dummy_payload = {
            "version": "1.0.0",
            "entities": {},
            "relationships": [],
            "metrics": {},
            "security_policies": {"roles": {}}
        }
        response = self.client.post("/api/config/semantic-layer", json=dummy_payload, headers=self.doctor_headers)
        self.assertEqual(response.status_code, 403)

    def test_post_config_validation_failure_table(self):
        """Saving config with non-existent table must return 400."""
        invalid_payload = {
            "version": "1.0.0",
            "entities": {
                "FakeEntity": {
                    "table_name": "non_existent_table",
                    "primary_key": "id",
                    "description": "Fake",
                    "fields": {}
                }
            },
            "relationships": [],
            "metrics": {},
            "security_policies": {"roles": {}}
        }
        response = self.client.post("/api/config/semantic-layer", json=invalid_payload, headers=self.admin_headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn("does not exist in database", response.json()["detail"])

    def test_post_config_validation_failure_column(self):
        """Saving config with non-existent column must return 400."""
        invalid_payload = {
            "version": "1.0.0",
            "entities": {
                "Patient": {
                    "table_name": "patients",
                    "primary_key": "fake_id_column",
                    "description": "Patients",
                    "fields": {}
                }
            },
            "relationships": [],
            "metrics": {},
            "security_policies": {"roles": {}}
        }
        response = self.client.post("/api/config/semantic-layer", json=invalid_payload, headers=self.admin_headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn("does not exist in table", response.json()["detail"])

    def test_post_config_success_and_hot_reload(self):
        """Successfully saving config must update file and hot-reload model mappings."""
        # Read active configuration first to base our new configuration on it
        response = self.client.get("/api/config/semantic-layer", headers=self.admin_headers)
        current_config = response.json()["config"]
        
        # Modify predefined metrics to include a dummy test metric
        current_config["metrics"]["Test Predefined Count"] = {
            "description": "Admissions query metric for testing configurations",
            "formula": "SELECT COUNT(*) FROM encounters"
        }
        
        # POST the updated configuration
        response = self.client.post("/api/config/semantic-layer", json=current_config, headers=self.admin_headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        
        # Verify the file was written
        with open(self.yaml_path, "r", encoding="utf-8") as f:
            updated_yaml = yaml.safe_load(f)
        self.assertIn("Test Predefined Count", updated_yaml["metrics"])
        
        # Verify hot-reload by checking agent's semantic layer prompt
        from backend.app import agent
        prompt = agent.semantic_layer.get_context_prompt()
        self.assertIn("Test Predefined Count", prompt)

if __name__ == "__main__":
    unittest.main()
