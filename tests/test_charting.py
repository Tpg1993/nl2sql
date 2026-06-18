import unittest

class TestVisualChartingDataForm(unittest.TestCase):
    
    def is_chartable_python(self, data):
        """Python implementation of frontend isChartable() logic for backend validation."""
        if not isinstance(data, list) or len(data) == 0:
            return False
        first_row = data[0]
        if not isinstance(first_row, dict):
            return False
            
        has_numeric = False
        for k, val in first_row.items():
            if isinstance(val, (int, float)):
                has_numeric = True
            elif isinstance(val, str):
                try:
                    num = float(val)
                    has_numeric = True
                except ValueError:
                    pass
        return has_numeric

    def test_chartable_datasets(self):
        """Verifies that typical numeric/categorical datasets are parsed as chartable."""
        # Categorical labels + Numeric counts
        dataset_1 = [
            {"severity": "High", "allergy_count": 5},
            {"severity": "Medium", "allergy_count": 12},
            {"severity": "Low", "allergy_count": 28}
        ]
        self.assertTrue(self.is_chartable_python(dataset_1))

        # Float values
        dataset_2 = [
            {"department_name": "Emergency", "total_charges": 14250.50},
            {"department_name": "Cardiology", "total_charges": 98200.75}
        ]
        self.assertTrue(self.is_chartable_python(dataset_2))

        # String numeric representation
        dataset_3 = [
            {"year": "2023", "encounter_count": "142"},
            {"year": "2024", "encounter_count": "285"}
        ]
        self.assertTrue(self.is_chartable_python(dataset_3))

    def test_non_chartable_datasets(self):
        """Verifies that text-only datasets are correctly identified as non-chartable."""
        # Strings only
        dataset_1 = [
            {"first_name": "John", "last_name": "Doe"},
            {"first_name": "Alice", "last_name": "Smith"}
        ]
        self.assertFalse(self.is_chartable_python(dataset_1))

        # Empty array
        self.assertFalse(self.is_chartable_python([]))

        # Non-list values
        self.assertFalse(self.is_chartable_python(None))
        self.assertFalse(self.is_chartable_python("raw text result"))

if __name__ == "__main__":
    unittest.main()
