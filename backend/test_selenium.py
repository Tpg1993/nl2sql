import unittest
import subprocess
import sys
import time
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv

# Load environment variables
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

class TestEHRQueryAgentSelenium(unittest.TestCase):
    backend_process = None
    frontend_process = None
    backend_log = None
    frontend_log = None
    driver = None
    log_file = None

    @classmethod
    def setUpClass(cls):
        BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        log_path = os.path.join(BASE_DIR, "selenium_test_results.log")
        cls.log_file = open(log_path, "w", encoding="utf-8")
        
        cls.log("=== STARTING SELENIUM E2E INTEGRATION TESTS ===")
        
        # Backup semantic_layer.yaml
        cls.yaml_path = os.path.join(BASE_DIR, "semantic_layer.yaml")
        cls.backup_path = os.path.join(BASE_DIR, "semantic_layer.yaml.backup")
        if os.path.exists(cls.yaml_path):
            try:
                import shutil
                shutil.copy2(cls.yaml_path, cls.backup_path)
                cls.log("Successfully backed up semantic_layer.yaml.")
            except Exception as e:
                cls.log(f"WARNING: Could not backup semantic_layer.yaml: {e}")
        
        # Clear the cache.db file to ensure fresh cache state isolation
        cache_db_path = os.path.abspath(os.path.join(BASE_DIR, "cache.db"))
        if os.path.exists(cache_db_path):
            try:
                cls.log(f"Clearing cache database at {cache_db_path}...")
                os.remove(cache_db_path)
            except Exception as e:
                cls.log(f"Could not delete cache database: {e}")
        
        # 1. Start FastAPI backend, logging output to uvicorn.log
        cls.log("Launching backend uvicorn server on http://127.0.0.1:8000...")
        cls.backend_log = open(os.path.join(BASE_DIR, "uvicorn.log"), "w", encoding="utf-8")
        env = os.environ.copy()
        env["TESTING"] = "true"
        cls.backend_process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1", "--port", "8000"],
            cwd=os.path.abspath(os.path.join(BASE_DIR, "..")),
            stdout=cls.backend_log, stderr=cls.backend_log,
            env=env
        )
        
        # 2. Start Frontend server, logging output to frontend.log
        cls.log("Launching frontend python http server on http://localhost:3000...")
        cls.frontend_log = open(os.path.join(BASE_DIR, "frontend.log"), "w", encoding="utf-8")
        cls.frontend_process = subprocess.Popen(
            [sys.executable, "-m", "http.server", "3000", "--directory", "frontend"],
            cwd=os.path.abspath(os.path.join(BASE_DIR, "..")),
            stdout=cls.frontend_log, stderr=cls.frontend_log
        )
        
        # Poll servers to make sure they are listening before starting Chrome
        cls.log("Waiting for backend uvicorn server to start listening...")
        import urllib.request
        from urllib.error import URLError, HTTPError
        
        backend_ready = False
        for i in range(45):
            try:
                urllib.request.urlopen("http://127.0.0.1:8000/api/metadata", timeout=1)
                backend_ready = True
                cls.log(f"Backend responded successfully after {i} seconds.")
                break
            except HTTPError as e:
                # HTTPError (like 401 Unauthorized) means backend is running and listening!
                backend_ready = True
                cls.log(f"Backend responded with status code {e.code} after {i} seconds.")
                break
            except URLError:
                time.sleep(1)
                
        if not backend_ready:
            cls.log("WARNING: Backend uvicorn server did not become responsive within 45 seconds!")
            
        cls.log("Waiting for frontend python http server to start listening...")
        frontend_ready = False
        for i in range(15):
            try:
                urllib.request.urlopen("http://localhost:3000", timeout=1)
                frontend_ready = True
                cls.log(f"Frontend responded successfully after {i} seconds.")
                break
            except URLError:
                time.sleep(1)
            except Exception:
                frontend_ready = True
                break
                
        if not frontend_ready:
            cls.log("WARNING: Frontend server did not become responsive within 15 seconds!")
        
        # 3. Start headless Chrome
        cls.log("Initializing headless Chrome WebDriver...")
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        cls.driver = webdriver.Chrome(options=options)
        cls.driver.set_window_size(1400, 900)

    @classmethod
    def tearDownClass(cls):
        cls.log("\n=== TEARING DOWN INTEGRATION SERVERS ===")
        if cls.driver:
            cls.driver.quit()
        if cls.backend_process:
            cls.log("Terminating FastAPI backend process...")
            cls.backend_process.terminate()
            cls.backend_process.wait()
        if cls.backend_log:
            cls.backend_log.close()
        if cls.frontend_process:
            cls.log("Terminating Frontend uvicorn/http process...")
            cls.frontend_process.terminate()
            cls.frontend_process.wait()
        if cls.frontend_log:
            cls.frontend_log.close()
            
        # Restore semantic_layer.yaml
        if hasattr(cls, "backup_path") and os.path.exists(cls.backup_path):
            try:
                import shutil
                shutil.copy2(cls.backup_path, cls.yaml_path)
                os.remove(cls.backup_path)
                cls.log("Successfully restored semantic_layer.yaml.")
            except Exception as e:
                cls.log(f"WARNING: Could not restore semantic_layer.yaml: {e}")
                
        cls.log("=== SELENIUM TESTING SUITE COMPLETED ===")
        cls.log_file.close()

    @classmethod
    def log(cls, message: str):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] {message}"
        print(log_line)
        if cls.log_file:
            cls.log_file.write(log_line + "\n")
            cls.log_file.flush()

    def setUp(self):
        self._test_has_failed = True
        # Fresh page load before each test case
        self.driver.get("http://localhost:3000")
        time.sleep(1)

    def tearDown(self):
        # Capture screenshot on failure
        if getattr(self, "_test_has_failed", True):
            try:
                screenshot_name = f"failure_{self._testMethodName}.png"
                workspace_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
                screenshot_path = os.path.join(workspace_dir, screenshot_name)
                self.driver.save_screenshot(screenshot_path)
                self.log(f"-> Captured failure screenshot to: {screenshot_name}")
            except Exception as e:
                self.log(f"-> Failed to capture screenshot: {e}")
                
        # Clean state by logging out after each test
        try:
            self.logout()
        except Exception:
            pass

    def login(self, username, password):
        """Helper to input credentials and submit authentication."""
        driver = self.driver
        WebDriverWait(driver, 5).until(
            EC.visibility_of_element_located((By.ID, "login-overlay"))
        )
        
        username_input = driver.find_element(By.ID, "login-username")
        password_input = driver.find_element(By.ID, "login-password")
        submit_btn = driver.find_element(By.ID, "login-submit-btn")
        
        username_input.clear()
        username_input.send_keys(username)
        password_input.clear()
        password_input.send_keys(password)
        
        submit_btn.click()
        
        # Wait for modal overlay to fade out
        WebDriverWait(driver, 5).until(
            EC.invisibility_of_element_located((By.ID, "login-overlay"))
        )

    def logout(self):
        """Helper to clear local storage and session."""
        self.driver.execute_script("localStorage.removeItem('access_token');")
        self.driver.refresh()
        time.sleep(1)

    def test_1_invalid_login(self):
        self.log("[Test 1] Testing invalid authentication error display...")
        driver = self.driver
        WebDriverWait(driver, 5).until(
            EC.visibility_of_element_located((By.ID, "login-overlay"))
        )
        
        # Enter bad credentials
        driver.find_element(By.ID, "login-username").send_keys("invalid_user")
        driver.find_element(By.ID, "login-password").send_keys("wrong_password")
        driver.find_element(By.ID, "login-submit-btn").click()
        
        # Verify error message
        error_msg = WebDriverWait(driver, 5).until(
            EC.visibility_of_element_located((By.ID, "login-error"))
        )
        self.assertTrue(error_msg.is_displayed())
        self.log(f"-> SUCCESS: Invalid login correctly rejected with: '{error_msg.text}'")
        self._test_has_failed = False

    def test_2_admin_login_and_role_badge(self):
        self.log("[Test 2] Testing admin authentication and RBAC UI role badge...")
        self.login("admin", ADMIN_PASSWORD)
        
        # Verify role badge display
        role_badge = WebDriverWait(self.driver, 5).until(
            EC.visibility_of_element_located((By.ID, "user-role-badge"))
        )
        self.assertTrue(role_badge.is_displayed())
        role_text = self.driver.find_element(By.ID, "user-role-text").text
        self.assertEqual(role_text.lower(), "admin")
        self.log("-> SUCCESS: Admin role badge verified successfully.")
        self._test_has_failed = False
        self.logout()

    def test_3_sidebar_interaction(self):
        self.log("[Test 3] Testing sidebar schema loader and interactive click injection...")
        self.login("admin", ADMIN_PASSWORD)
        
        # Verify status text transitions to 'Connected'
        status_text = WebDriverWait(self.driver, 10).until(
            EC.text_to_be_present_in_element((By.ID, "status-text"), "Connected")
        )
        
        # Check if tables are loaded in sidebar
        table_container = self.driver.find_element(By.ID, "table-list-container")
        table_items = table_container.find_elements(By.CLASS_NAME, "table-item")
        self.assertGreater(len(table_items), 0)
        
        # Click on table helper name 'patients'
        patients_helper = table_container.find_element(By.XPATH, "//span[text()='patients']")
        patients_helper.click()
        
        # Verify input box value contains table name
        input_box = self.driver.find_element(By.ID, "user-input")
        self.assertIn("patients", input_box.get_attribute("value"))
        self.log("-> SUCCESS: Sidebar reflection and interactive click verified.")
        self._test_has_failed = False
        self.logout()

    def test_4_collapsible_sidebar(self):
        self.log("[Test 4] Testing collapsible sidebar panel and transitions...")
        self.login("admin", ADMIN_PASSWORD)
        
        app_container = self.driver.find_element(By.ID, "app-container")
        collapse_btn = self.driver.find_element(By.ID, "toggle-sidebar-collapse")
        expand_btn = self.driver.find_element(By.ID, "toggle-sidebar-expand")
        
        # 1. Collapse sidebar
        collapse_btn.click()
        time.sleep(1) # wait for animation
        self.assertIn("sidebar-collapsed", app_container.get_attribute("class"))
        self.assertTrue(expand_btn.is_displayed())
        
        # 2. Expand sidebar
        expand_btn.click()
        time.sleep(1)
        self.assertNotIn("sidebar-collapsed", app_container.get_attribute("class"))
        self.assertFalse(expand_btn.is_displayed())
        
        self.log("-> SUCCESS: Collapsible sidebar state transitions verified.")
        self._test_has_failed = False
        self.logout()

    def test_5_theme_toggle(self):
        self.log("[Test 5] Testing theme switcher light/dark persistence...")
        self.login("admin", ADMIN_PASSWORD)
        
        body = self.driver.find_element(By.TAG_NAME, "body")
        theme_btn = self.driver.find_element(By.ID, "theme-toggle-btn")
        
        # Toggle light theme
        theme_btn.click()
        self.assertEqual(body.get_attribute("data-theme"), "light")
        
        # Toggle back to dark theme
        theme_btn.click()
        self.assertEqual(body.get_attribute("data-theme"), "dark")
        
        self.log("-> SUCCESS: Theme switcher toggled successfully.")
        self._test_has_failed = False
        self.logout()

    def test_6_query_execution_and_standard_caching(self):
        self.log("[Test 6] Testing query compilation, data table rendering, and standard cache hits...")
        self.login("admin", ADMIN_PASSWORD)
        
        # Wait for connection
        WebDriverWait(self.driver, 10).until(
            EC.text_to_be_present_in_element((By.ID, "status-text"), "Connected")
        )
        
        # Submit query
        input_box = WebDriverWait(self.driver, 5).until(
            EC.element_to_be_clickable((By.ID, "user-input"))
        )
        input_box.clear()
        query_text = "How many patients do we have in total?"
        input_box.send_keys(query_text)
        
        send_btn = WebDriverWait(self.driver, 5).until(
            EC.element_to_be_clickable((By.ID, "send-button"))
        )
        send_btn.click()
        
        # Wait for result card to render (loader disappears)
        time.sleep(8)
        
        # Check table result is present
        result_table = WebDriverWait(self.driver, 45).until(
            EC.presence_of_element_located((By.CLASS_NAME, "result-table"))
        )
        self.assertTrue(result_table.is_displayed())
        
        # Resubmit query to verify cached hit
        input_box = WebDriverWait(self.driver, 5).until(
            EC.element_to_be_clickable((By.ID, "user-input"))
        )
        input_box.clear()
        input_box.send_keys(query_text)
        
        send_btn = WebDriverWait(self.driver, 5).until(
            EC.element_to_be_clickable((By.ID, "send-button"))
        )
        send_btn.click()
        
        time.sleep(3)
        
        # Find cache badge in last response card
        cache_badges = self.driver.find_elements(By.CLASS_NAME, "cache-badge")
        self.assertGreater(len(cache_badges), 0)
        self.log("-> SUCCESS: Query output rendered in HTML table and standard cache badge verified.")
        self._test_has_failed = False
        self.logout()

    def test_7_query_lineage(self):
        self.log("[Test 7] Testing lineage audit logs visual tree extraction...")
        self.login("admin", ADMIN_PASSWORD)
        
        # Submit query involving a join
        WebDriverWait(self.driver, 10).until(
            EC.text_to_be_present_in_element((By.ID, "status-text"), "Connected")
        )
        input_box = WebDriverWait(self.driver, 5).until(
            EC.element_to_be_clickable((By.ID, "user-input"))
        )
        input_box.clear()
        input_box.send_keys("What is the count of providers in each department? Show top 5.")
        
        send_btn = WebDriverWait(self.driver, 5).until(
            EC.element_to_be_clickable((By.ID, "send-button"))
        )
        send_btn.click()
        
        time.sleep(8)
        
        # Click view lineage details
        lineage_summary = WebDriverWait(self.driver, 45).until(
            EC.element_to_be_clickable((By.CLASS_NAME, "lineage-summary"))
        )
        lineage_summary.click()
        
        # Verify lineage badges
        table_nodes = self.driver.find_elements(By.CLASS_NAME, "table-node")
        self.assertGreater(len(table_nodes), 0)
        self.log("-> SUCCESS: Query lineage source tables and Badge projections verified.")
        self._test_has_failed = False
        self.logout()

    def test_8_expert_override_loop(self):
        self.log("[Test 8] Testing RLHF expert overrides & 'Expert Approved' status...")
        self.login("admin", ADMIN_PASSWORD)
        
        WebDriverWait(self.driver, 10).until(
            EC.text_to_be_present_in_element((By.ID, "status-text"), "Connected")
        )
        
        query_text = "Count the patients in the database"
        input_box = WebDriverWait(self.driver, 5).until(
            EC.element_to_be_clickable((By.ID, "user-input"))
        )
        input_box.clear()
        input_box.send_keys(query_text)
        
        send_btn = WebDriverWait(self.driver, 5).until(
            EC.element_to_be_clickable((By.ID, "send-button"))
        )
        send_btn.click()
        
        time.sleep(8)
        
        # Open suggest override form
        override_summary = WebDriverWait(self.driver, 45).until(
            EC.element_to_be_clickable((By.CLASS_NAME, "override-summary"))
        )
        override_summary.click()
        
        # Edit SQL text area
        textarea = self.driver.find_element(By.CLASS_NAME, "override-textarea")
        sql_text = textarea.get_attribute("value")
        textarea.clear()
        textarea.send_keys(sql_text + " -- expert validation")
        
        # Save correction
        self.driver.find_element(By.CLASS_NAME, "submit-override-btn").click()
        
        # Wait for status message
        status_msg = WebDriverWait(self.driver, 5).until(
            EC.visibility_of_element_located((By.CLASS_NAME, "override-status-msg"))
        )
        WebDriverWait(self.driver, 5).until(
            EC.text_to_be_present_in_element((By.CLASS_NAME, "override-status-msg"), "Override saved successfully!")
        )
        
        # Submit the same query again
        input_box = WebDriverWait(self.driver, 5).until(
            EC.element_to_be_clickable((By.ID, "user-input"))
        )
        input_box.clear()
        input_box.send_keys(query_text)
        
        send_btn = WebDriverWait(self.driver, 5).until(
            EC.element_to_be_clickable((By.ID, "send-button"))
        )
        send_btn.click()
        
        # Check expert approved badge in result (wait up to 15 seconds for LLM response)
        expert_badge = WebDriverWait(self.driver, 15).until(
            EC.presence_of_element_located((By.CLASS_NAME, "expert-badge"))
        )
        self.assertTrue(expert_badge.is_displayed())
        self.log("-> SUCCESS: SQL override loop registered and 'Expert Approved' badge displayed on reload.")
        self._test_has_failed = False
        self.logout()

    def test_9_compliance_pii_masking_researcher(self):
        self.log("[Test 9] Testing HIPAA compliance and PII masking for Researcher role...")
        self.login("researcher", "researcher123")
        
        WebDriverWait(self.driver, 10).until(
            EC.text_to_be_present_in_element((By.ID, "status-text"), "Connected")
        )
        
        # Query that returns patient name details
        input_box = WebDriverWait(self.driver, 5).until(
            EC.element_to_be_clickable((By.ID, "user-input"))
        )
        input_box.clear()
        input_box.send_keys("Give me the details of patients")
        
        send_btn = WebDriverWait(self.driver, 5).until(
            EC.element_to_be_clickable((By.ID, "send-button"))
        )
        send_btn.click()
        
        time.sleep(8)
        
        # Inspect table rows for masked lock icons
        result_table = WebDriverWait(self.driver, 45).until(
            EC.presence_of_element_located((By.CLASS_NAME, "result-table"))
        )
        
        # Check for lock icons or masked characters
        lock_icons = result_table.find_elements(By.CLASS_NAME, "fa-lock")
        self.assertGreater(len(lock_icons), 0)
        self.log(f"-> SUCCESS: HIPAA Dynamic RBAC masking verified. Found {len(lock_icons)} PII lock/masked cells.")
        self._test_has_failed = False
        self.logout()

    def test_10_semantic_configurator_admin(self):
        self.log("[Test 10] Testing Semantic Configurator Panel for Admin and RBAC visibility...")
        driver = self.driver
        
        # 1. Log in as researcher first and verify Settings button is NOT displayed
        self.login("researcher", "researcher123")
        WebDriverWait(driver, 10).until(
            EC.text_to_be_present_in_element((By.ID, "status-text"), "Connected")
        )
        settings_btn = driver.find_element(By.ID, "settings-toggle-btn")
        self.assertFalse(settings_btn.is_displayed(), "Settings button should be hidden for Researcher role.")
        self.log("-> SUCCESS: Verified settings button is hidden for non-admin user.")
        self.logout()
        
        # 2. Log in as admin and verify Settings button IS displayed
        self.login("admin", ADMIN_PASSWORD)
        WebDriverWait(driver, 10).until(
            EC.text_to_be_present_in_element((By.ID, "status-text"), "Connected")
        )
        settings_btn = WebDriverWait(driver, 5).until(
            EC.visibility_of_element_located((By.ID, "settings-toggle-btn"))
        )
        self.assertTrue(settings_btn.is_displayed(), "Settings button should be visible for Admin role.")
        
        # 3. Click Settings to open drawer
        settings_btn.click()
        overlay = WebDriverWait(driver, 5).until(
            EC.visibility_of_element_located((By.ID, "settings-overlay"))
        )
        self.assertTrue(overlay.is_displayed(), "Settings drawer overlay should be visible after click.")
        self.log("-> SUCCESS: Opened Semantic Configurator drawer.")

        # Test Auto-Discover button on default Entities tab
        discover_btn = driver.find_element(By.ID, "discover-schema-btn")
        discover_btn.click()
        time.sleep(1)
        alert = driver.switch_to.alert
        alert.accept()
        time.sleep(2)
        WebDriverWait(driver, 10).until(
            EC.text_to_be_present_in_element((By.ID, "settings-status-msg"), "discovered successfully")
        )
        self.log("-> SUCCESS: Verified Auto-Discover schema mapping.")
        
        # 4. Navigate to Metrics tab and verify content
        metrics_tab_btn = driver.find_element(By.XPATH, "//button[@data-tab='tab-metrics']")
        metrics_tab_btn.click()
        time.sleep(1)
        
        # Check initial metric cards are visible
        metric_cards = driver.find_elements(By.CLASS_NAME, "metric-card")
        self.assertGreater(len(metric_cards), 0)
        self.log(f"-> Verified {len(metric_cards)} metric cards are rendered.")
        
        # 5. Click Add Metric
        add_metric_btn = driver.find_element(By.ID, "add-metric-btn")
        add_metric_btn.click()
        time.sleep(1)
        
        # Verify a new card is added
        new_metric_cards = driver.find_elements(By.CLASS_NAME, "metric-card")
        self.assertEqual(len(new_metric_cards), len(metric_cards) + 1, "A new metric card should be appended.")
        
        # 6. Fill in the newly added card (last card)
        last_card = new_metric_cards[-1]
        name_input = last_card.find_element(By.CLASS_NAME, "metric-name")
        formula_textarea = last_card.find_element(By.CLASS_NAME, "metric-formula")
        desc_input = last_card.find_element(By.CLASS_NAME, "metric-description")
        
        name_input.send_keys("Selenium Dynamic Metric")
        formula_textarea.send_keys("SELECT COUNT(*) FROM vitals")
        desc_input.send_keys("Metric compiled by Selenium E2E test suite")

        # Test Formula validation check in UI
        test_formula_btn = last_card.find_element(By.CLASS_NAME, "test-metric-btn")
        test_formula_btn.click()
        WebDriverWait(driver, 10).until(
            EC.text_to_be_present_in_element((By.XPATH, "(//span[contains(@class, 'test-feedback-msg')])[last()]"), "Valid!")
        )
        self.log("-> SUCCESS: Verified metric SQL dry-run compiler check from UI.")
        
        # 7. Save and Hot-Reload
        save_btn = driver.find_element(By.ID, "save-settings-btn")
        save_btn.click()
        
        # Wait for status message indicating success
        status_msg = WebDriverWait(driver, 10).until(
            EC.visibility_of_element_located((By.ID, "settings-status-msg"))
        )
        WebDriverWait(driver, 10).until(
            EC.text_to_be_present_in_element((By.ID, "settings-status-msg"), "saved & hot-reloaded")
        )
        self.log("-> SUCCESS: Semantic config changes saved and hot-reloaded successfully.")
        
        # 8. Close drawer
        close_btn = driver.find_element(By.ID, "close-settings-btn")
        close_btn.click()
        
        # Wait for drawer to close
        WebDriverWait(driver, 5).until(
            EC.invisibility_of_element_located((By.ID, "settings-overlay"))
        )
        self.log("-> SUCCESS: Closed configurator drawer.")
        self._test_has_failed = False
        self.logout()

if __name__ == "__main__":
    unittest.main()

