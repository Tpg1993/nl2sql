import os
import sys
import unittest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

# Add parent directory to path so backend can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.agent import EHRQueryAgent
from backend.app import app
from backend.auth import create_access_token
from langchain_core.messages import AIMessage, HumanMessage

class TestConversationalMemory(unittest.TestCase):
    def setUp(self):
        # Run in testing mode to bypass real API calls
        os.environ["TESTING"] = "true"
        self.agent = EHRQueryAgent()

    def test_session_isolation_and_checkpointer(self):
        """Test that different thread_ids maintain separate conversation states."""
        thread_a = "thread-session-a"
        thread_b = "thread-session-b"

        # Mock LLM to return predictable responses
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(
            content="SELECT COUNT(*) FROM patients",
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        )
        self.agent.llm = mock_llm

        # Run query detailed for thread A
        self.agent.query_detailed("How many patients?", thread_id=thread_a)
        
        # Verify the query and state in checkpointer for thread A
        state_a = self.agent.agent.get_state({"configurable": {"thread_id": thread_a}})
        messages_a = state_a.values.get("messages", [])
        
        # Should have human query, retrieve schema, generated SQL, execution result, and summary
        self.assertTrue(len(messages_a) >= 4)
        self.assertEqual(messages_a[0].content, "How many patients?")

        # Check thread B is empty/independent originally
        state_b = self.agent.agent.get_state({"configurable": {"thread_id": thread_b}})
        self.assertEqual(state_b.values, {})

    def test_sliding_window_and_result_stripping(self):
        """Test that generate_query_node correctly compiles a sliding context window of up to 3 turns and completely strips raw DB results."""
        state = {
            "messages": [
                # Turn 1
                HumanMessage(content="Turn 1 Question"),
                AIMessage(content="CREATE TABLE patients (patient_id INT)"), # Schema msg
                AIMessage(content="SELECT * FROM patients WHERE age > 50"), # SQL msg
                AIMessage(content="[(1,), (2,)]"), # Raw DB result
                AIMessage(content="Turn 1 Summary"), # Summary msg
                
                # Turn 2
                HumanMessage(content="Turn 2 Question"),
                AIMessage(content="CREATE TABLE patients (patient_id INT)"), # Schema msg
                AIMessage(content="SELECT * FROM patients WHERE gender = 'F'"), # SQL msg
                AIMessage(content="[(3,), (4,)]"), # Raw DB result
                AIMessage(content="Turn 2 Summary"), # Summary msg

                # Turn 3
                HumanMessage(content="Turn 3 Question"),
                AIMessage(content="CREATE TABLE patients (patient_id INT)"), # Schema msg
                AIMessage(content="SELECT * FROM patients WHERE city = 'Boston'"), # SQL msg
                AIMessage(content="[(5,)]"), # Raw DB result
                AIMessage(content="Turn 3 Summary"), # Summary msg

                # Turn 4
                HumanMessage(content="Turn 4 Question"),
                AIMessage(content="CREATE TABLE patients (patient_id INT)"), # Schema msg
                AIMessage(content="SELECT * FROM patients WHERE state = 'MA'"), # SQL msg
                AIMessage(content="[(6,)]"), # Raw DB result
                AIMessage(content="Turn 4 Summary"), # Summary msg

                # Turn 5 (Latest query)
                HumanMessage(content="Turn 5 Question"),
            ],
            "retries": 0,
            "latencies": {},
            "user_context": {}
        }

        # Mock prompt_library.format_sql_generation to inspect chat_history parameter
        self.agent.prompt_library.format_sql_generation = MagicMock(return_value="formatted prompt")
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(content="SELECT * FROM patients")
        self.agent.llm = mock_llm
        
        # Invoke generate_query_node directly
        self.agent.generate_query_node(state)

        # Inspect what was passed to format_sql_generation
        called_args, called_kwargs = self.agent.prompt_library.format_sql_generation.call_args
        chat_history = called_kwargs.get("chat_history", "")

        # Verify:
        # 1. Turn 1 should be stripped out (sliding window of 3 turns)
        self.assertNotIn("Turn 1 Question", chat_history)
        self.assertNotIn("Turn 1 Summary", chat_history)
        
        # 2. Turns 2, 3, 4 should be present in the chat history
        self.assertIn("Turn 2 Question", chat_history)
        self.assertIn("Turn 2 Summary", chat_history)
        self.assertIn("Turn 3 Question", chat_history)
        self.assertIn("Turn 3 Summary", chat_history)
        self.assertIn("Turn 4 Question", chat_history)
        self.assertIn("Turn 4 Summary", chat_history)
        
        # 3. Raw database results [(3,), (4,)], [(5,)], and [(6,)] must be completely stripped out
        self.assertNotIn("[(3,), (4,)]", chat_history)
        self.assertNotIn("[(5,)]", chat_history)
        self.assertNotIn("[(6,)]", chat_history)

        # 4. Schemas (CREATE TABLE) must be completely stripped out from history
        self.assertNotIn("CREATE TABLE", chat_history)


class TestConversationalHistoryAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["TESTING"] = "true"
        cls.client = TestClient(app)
        
        # Generate tokens
        admin_token = create_access_token(data={"sub": "admin", "role": "admin", "attributes": {}})
        researcher_token = create_access_token(data={"sub": "researcher", "role": "researcher", "attributes": {}})
        
        cls.admin_headers = {"Authorization": f"Bearer {admin_token}"}
        cls.researcher_headers = {"Authorization": f"Bearer {researcher_token}"}

    def test_history_unauthorized(self):
        """Unauthenticated requests to history endpoint must fail with 401."""
        response = self.client.get("/api/history/some_thread_id")
        self.assertEqual(response.status_code, 401)

    def test_history_success_and_abac_masking(self):
        """Verify that history is retrieved and parsed correctly, with ABAC masking applied for researchers."""
        thread_id = "test_api_history_thread_1"

        # 1. Execute a query as admin to populate the checkpointer state
        query_payload = {
            "question": "Show details of patient Alice Green",
            "thread_id": thread_id
        }
        
        # Mock LLM to return patient query and details
        from backend.app import agent as app_agent
        original_llm = app_agent.llm
        
        mock_llm = MagicMock()
        # Mock SQL generation first, then Mock summarization
        mock_llm.invoke.side_effect = [
            AIMessage(content="SELECT full_name, dob FROM patients"), # SQL Gen
            AIMessage(content="Here are the patient details for Alice Green.") # Summarization
        ]
        app_agent.llm = mock_llm
        
        try:
            # Run query as admin (unmasked)
            response = self.client.post("/api/query", json=query_payload, headers=self.admin_headers)
            self.assertEqual(response.status_code, 200)
            
            # 2. Query the history endpoint as Admin
            history_response = self.client.get(f"/api/history/{thread_id}", headers=self.admin_headers)
            self.assertEqual(history_response.status_code, 200)
            history_data = history_response.json()
            
            self.assertTrue(history_data.get("success"))
            self.assertEqual(len(history_data["history"]), 1)
            turn = history_data["history"][0]
            self.assertEqual(turn["question"], "Show details of patient Alice Green")
            self.assertIn("SELECT", turn["query"])
            
            # Since admin is retrieving, the result should have unmasked patient names (no asterisks)
            self.assertNotIn("*", str(turn["result"]))

            # 3. Query the history endpoint as Researcher
            researcher_history_resp = self.client.get(f"/api/history/{thread_id}", headers=self.researcher_headers)
            self.assertEqual(researcher_history_resp.status_code, 200)
            researcher_history_data = researcher_history_resp.json()
            
            researcher_turn = researcher_history_data["history"][0]
            # The researcher should receive masked names (containing asterisks)
            self.assertIn("*", str(researcher_turn["result"]))
            
        finally:
            # Restore original LLM
            app_agent.llm = original_llm

if __name__ == "__main__":
    unittest.main()
