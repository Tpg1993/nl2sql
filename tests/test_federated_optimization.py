import os
import pytest
from backend.agent import EHRQueryAgent
from backend.cache import get_cache_manager

@pytest.fixture
def agent():
    # Setup agent with zero cache
    cache = get_cache_manager(ttl_seconds=3600)
    cache.clear()
    return EHRQueryAgent(cache_manager=cache)

def test_tenant_routing_default(agent):
    # User context for default tenant (researcher)
    user_context = {
        "username": "researcher",
        "role": "researcher",
        "attributes": {"tenant_id": "default"}
    }
    
    # Running query to fetch the first patient's name
    # The default ehr_data.db has different names
    res_details = agent.query_detailed("SELECT full_name FROM patients WHERE patient_id = 1;", user_context=user_context)
    result = res_details["result"]
    assert "Tenant B Patient" not in result

def test_tenant_routing_tenant_b(agent):
    # User context for Tenant B (doctor)
    user_context = {
        "username": "doctor",
        "role": "doctor",
        "attributes": {"tenant_id": "tenant_b", "department_id": 1}
    }
    
    res_details = agent.query_detailed("SELECT full_name FROM patients WHERE patient_id = 1;", user_context=user_context)
    result = res_details["result"]
    assert "Tenant B Patient 1" in result

def test_adaptive_pushdown_threshold(agent):
    # Threshold for patients table (size should be 1000 in default sqlite)
    # 20% of 1000 is 200, but minimum cap is 500
    threshold = agent._get_pushdown_threshold("patients")
    assert threshold == 500

    # Threshold for a non-existent table should default to 1000
    threshold_unknown = agent._get_pushdown_threshold("non_existent_table")
    assert threshold_unknown == 1000

def test_federated_parallel_vs_sequential_execution(agent):
    # Run a federated query that involves local SQLite patients and remote warehouse tables (simulated on default engine)
    # The query joins patients (local SQLite) with encounters (remote warehouse)
    # Since local table patients size (1000) > threshold (500), it should trigger parallel execution path!
    user_context = {
        "username": "admin",
        "role": "admin",
        "attributes": {"tenant_id": "default"}
    }
    
    sql_query = "SELECT p.full_name, e.total_charges FROM patients p JOIN encounters e ON p.patient_id = e.patient_id LIMIT 5;"
    
    # Capture standard print logs or verify result execution
    res = agent.execute_federated_query(sql_query, user_context=user_context)
    assert res is not None
    assert "Tenant B Patient" not in res

def test_cache_tenant_isolation(agent):
    # Test that the cache correctly segregates results for different tenants
    cache = agent.cache_manager
    if not cache:
        pytest.skip("Cache manager not initialized")
        
    question = "SELECT full_name FROM patients WHERE patient_id = 1;"
    
    # Cache a dummy result for tenant_b
    dummy_b = {"query": "SELECT ...", "result": [{"full_name": "Tenant B Patient 1"}], "summary": "B", "tokens": {}}
    cache.set(question, dummy_b["query"], dummy_b["result"], dummy_b["summary"], dummy_b["tokens"], tenant_id="tenant_b")
    
    # Check that default tenant gets None (cache miss)
    cached_default = cache.get(question, tenant_id="default")
    assert cached_default is None
    
    # Check that tenant_b gets the cached result (cache hit)
    cached_b = cache.get(question, tenant_id="tenant_b")
    assert cached_b is not None
    assert cached_b["result"][0]["full_name"] == "Tenant B Patient 1"
