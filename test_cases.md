# EHR Agent Verification & Test Cases

This document lists categorized test cases (from simple counts to multi-table joins) to verify the behavior and accuracy of the NL2SQL Agent. You can copy-paste these questions directly into the [Chat Dashboard (http://localhost:3000)](http://localhost:3000) to test the agent.

---

## 1. Simple Queries (Single Table & Basic Filters)

### Test Case 1.1: Total Patient Count
*   **Question**: `How many patients are registered in total?`
*   **Expected SQL**:
    ```sql
    SELECT COUNT(*) FROM patients;
    ```
*   **Why it tests**: Checks simple table-level aggregation capability.

### Test Case 1.2: Specialty Enumeration
*   **Question**: `What are the distinct specialties of providers available?`
*   **Expected SQL**:
    ```sql
    SELECT DISTINCT specialty FROM providers;
    ```
*   **Why it tests**: Checks distinct value collection mapping.

### Test Case 1.3: Encounter Status Filtering
*   **Question**: `List all encounters where the status is 'Discharged'.`
*   **Expected SQL**:
    ```sql
    SELECT * FROM encounters WHERE encounter_status = 'Discharged';
    ```
*   **Why it tests**: Checks string filter accuracy.

---

## 2. Medium Queries (2-Table Joins & Grouping)

### Test Case 2.1: Top 5 Expensive Encounters with Patient Names
*   **Question**: `List the top 5 most expensive encounters showing the patient's full name and charges.`
*   **Expected SQL**:
    ```sql
    SELECT p.full_name, e.total_charges 
    FROM encounters e 
    JOIN patients p ON e.patient_id = p.patient_id 
    ORDER BY e.total_charges DESC 
    LIMIT 5;
    ```
*   **Why it tests**: Verifies basic primary-foreign key joining and sorting.

### Test Case 2.2: Active Allergies by Severity
*   **Question**: `What is the count of active allergies grouped by severity?`
*   **Expected SQL**:
    ```sql
    SELECT severity, COUNT(*) as allergy_count 
    FROM allergies 
    WHERE is_active = 1 
    GROUP BY severity;
    ```
*   **Why it tests**: Verifies multi-condition filters (boolean checks) combined with grouping.

### Test Case 2.3: Provider Performance
*   **Question**: `Which provider has handled the highest number of encounters? Show their code and count.`
*   **Expected SQL**:
    ```sql
    SELECT p.provider_code, COUNT(e.encounter_id) as encounter_count 
    FROM encounters e 
    JOIN providers p ON e.provider_id = p.provider_id 
    GROUP BY p.provider_id, p.provider_code 
    ORDER BY encounter_count DESC 
    LIMIT 1;
    ```
*   **Why it tests**: Verifies complex aggregation, grouping on keys, sorting, and limiting results.

---

## 3. Complex Queries (Multi-Table Joins & Function Transformations)

### Test Case 3.1: Common Diagnoses for Female Patients
*   **Question**: `List the top 3 most common diagnoses for female patients.`
*   **Expected SQL**:
    ```sql
    SELECT d.diagnosis_name, COUNT(*) as diagnosis_count 
    FROM diagnoses d 
    JOIN patients p ON d.patient_id = p.patient_id 
    WHERE p.gender = 'Female' 
    GROUP BY d.diagnosis_name 
    ORDER BY diagnosis_count DESC 
    LIMIT 3;
    ```
*   **Why it tests**: Tests column joins across entity scopes (clinical diagnoses joined with patient demographic details) alongside text conditions.

### Test Case 3.2: Average BMI by Blood Group
*   **Question**: `What is the average body mass index (BMI) of patients grouped by blood group?`
*   **Expected SQL**:
    ```sql
    SELECT p.blood_group, AVG(v.bmi) as average_bmi 
    FROM vitals v 
    JOIN patients p ON v.patient_id = p.patient_id 
    GROUP BY p.blood_group;
    ```
*   **Why it tests**: Joins metric vitals recording data with patient demographics, checking floating-point math aggregation.

### Test Case 3.3: Active Penicillin Allergies
*   **Question**: `What are the full names and phone numbers of patients who have active allergies to Penicillin?`
*   **Expected SQL**:
    ```sql
    SELECT p.full_name, p.phone 
    FROM patients p 
    JOIN allergies a ON p.patient_id = a.patient_id 
    WHERE a.allergen_name LIKE '%Penicillin%' AND a.is_active = 1;
    ```
*   **Why it tests**: Verifies fuzzy text pattern matching (`LIKE '%...%'`) combined with state boolean conditions.

### Test Case 3.4: Department Provider & Encounter Statistics
*   **Question**: `List all departments, showing their name, the total number of providers, and the total number of encounters handled in each.`
*   **Expected SQL**:
    ```sql
    SELECT d.department_name, COUNT(DISTINCT pr.id) as provider_count, COUNT(e.id) as encounter_count
    FROM departments d
    LEFT JOIN providers pr ON d.id = pr.department_id
    LEFT JOIN encounters e ON pr.id = e.provider_id
    GROUP BY d.id, d.department_name;
    ```
*   **Why it tests**: Verifies three-table joins with count aggregations and proper grouping.

### Test Case 3.5: Lisinopril and Hypertension Patient Count
*   **Question**: `What is the count of patients diagnosed with 'Essential hypertension' who are currently taking 'Lisinopril'?`
*   **Expected SQL**:
    ```sql
    SELECT COUNT(DISTINCT p.id) 
    FROM patients p
    JOIN diagnoses d ON p.id = d.patient_id
    JOIN medications m ON p.id = m.patient_id
    WHERE d.description = 'Essential hypertension' AND m.name = 'Lisinopril';
    ```
*   **Why it tests**: Requires joining the patient demographics table with two separate clinical log tables (diagnoses and medications) simultaneously.

---

## 4. Advanced Self-Healing & Dialect-Specific Retries

These queries are intentionally designed to challenge the agent's schema mapping or SQL dialect execution. They trigger the backend self-healing loops to heal syntax/column discrepancies and execute successfully on retry (registering as `Retries: 1` or `2` in the UI).

### Test Case 4.1: Concatenation and Ambiguous Column Names
*   **Question**: `List the patient name and provider name for encounters in 2023.`
*   **Why it triggers retry**: The LLM will initially generate a query selecting `patients.name` or `providers.name` (which do not exist in the database). The agent catches the database error, inspects the schema to see that patient names consist of `first_name` and `last_name`, and corrects the query to use concatenation (e.g. `p.first_name || ' ' || p.last_name`).
*   **Expected Resolved SQL**:
    ```sql
    SELECT p.first_name || ' ' || p.last_name AS patient_name, pr.first_name || ' ' || pr.last_name AS provider_name 
    FROM encounters e 
    JOIN patients p ON e.patient_id = p.id 
    JOIN providers pr ON e.provider_id = pr.id 
    WHERE strftime('%Y', e.encounter_date) = '2023';
    ```

### Test Case 4.2: Dialect-Specific Date Calculations
*   **Question**: `What is the average age of patients diagnosed with Hypertension?`
*   **Why it triggers retry**: The LLM may initially output standard SQL functions like `DATEDIFF` or `AGE()` which are incompatible with SQLite. The database run throws a syntax error, which the agent catches and corrects using SQLite-specific date operations (e.g. `strftime('%Y', 'now') - strftime('%Y', birth_date)`).
*   **Expected Resolved SQL**:
    ```sql
    SELECT AVG(strftime('%Y', 'now') - strftime('%Y', p.birth_date)) AS average_age
    FROM patients p
    JOIN diagnoses d ON p.id = d.patient_id
    WHERE d.description LIKE '%Hypertension%';
    ```
