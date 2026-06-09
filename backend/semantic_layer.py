import os
import yaml
from typing import Dict, Any, List

class SemanticLayer:
    """Loads and parses semantic_layer.yaml, exposing structured metadata
    and context compilation utilities to prevent LLM query hallucinations.
    """

    def __init__(self, config_path: str = None) -> None:
        if config_path is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            config_path = os.path.join(base_dir, "semantic_layer.yaml")
        
        self.config_path = config_path
        self.config = self._load_config()
        self.entities = self.config.get("entities", {})
        self.relationships = self.config.get("relationships", [])
        self.metrics = self.config.get("metrics", {})
        self.security_policies = self.config.get("security_policies", {})

    def _load_config(self) -> Dict[str, Any]:
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"Semantic configuration not found at: {self.config_path}")
        with open(self.config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def get_context_prompt(self) -> str:
        """Generates a text description of the semantic model structure to
        inject into LLM generation prompts.
        """
        prompt = "SEMANTIC LAYER DEFINITIONS (Use these definitions instead of guessing schema columns):\n\n"
        
        prompt += "1. ENTITIES:\n"
        for entity_name, details in self.entities.items():
            prompt += f"  - Entity: {entity_name} (Physical Table: '{details.get('table_name')}', Primary Key: '{details.get('primary_key')}')\n"
            prompt += f"    Description: {details.get('description', '')}\n"
            prompt += "    Logical to Physical Field Mappings:\n"
            fields = details.get("fields", {})
            for logical, physical in fields.items():
                col_name = physical.get("column_name") if isinstance(physical, dict) else physical
                prompt += f"      * {logical} -> {col_name}\n"
            prompt += "\n"

        prompt += "2. RELATIONSHIPS / JOINS (Always join tables using these rules):\n"
        for rel in self.relationships:
            prompt += f"  - Join '{self.entities[rel['from_entity']]['table_name']}' with '{self.entities[rel['to_entity']]['table_name']}'\n"
            prompt += f"    ON {self.entities[rel['from_entity']]['table_name']}.{rel['join_keys']['from_key']} = {self.entities[rel['to_entity']]['table_name']}.{rel['join_keys']['to_key']}\n"
        prompt += "\n"

        prompt += "3. PREDEFINED METRICS (Use these exact subqueries/formulas when users ask for these metrics):\n"
        for metric_name, m_details in self.metrics.items():
            prompt += f"  - Metric: '{metric_name}'\n"
            prompt += f"    Description: {m_details.get('description', '')}\n"
            prompt += f"    Formula: {m_details.get('formula', '')}\n"
            if "filters" in m_details:
                prompt += "    Specific filters:\n"
                for filt_name, filt_val in m_details["filters"].items():
                    prompt += f"      * {filt_name}: {filt_val}\n"
            prompt += "\n"

        return prompt

    def get_field_mapping(self, entity_name: str, logical_field: str) -> str:
        """Returns the physical column name for a given entity and logical field."""
        entity = self.entities.get(entity_name)
        if not entity:
            return None
        val = entity.get("fields", {}).get(logical_field)
        if isinstance(val, dict):
            return val.get("column_name")
        return val

    def get_table_name(self, entity_name: str) -> str:
        """Returns the physical table name for a given entity."""
        entity = self.entities.get(entity_name)
        if not entity:
            return None
        return entity.get("table_name")

    def get_column_classification(self, table_name: str, column_name: str) -> str:
        """Scans entities to find the classification of a physical column in a physical table."""
        # Clean potential table prefixes from column_name (e.g. patients.full_name -> full_name)
        clean_col = column_name.split('.')[-1].strip('`"\'')
        for entity_name, details in self.entities.items():
            if details.get("table_name") == table_name:
                for logical, field_val in details.get("fields", {}).items():
                    if isinstance(field_val, dict):
                        if field_val.get("column_name") == clean_col:
                            return field_val.get("classification")
                    elif field_val == clean_col:
                        return None
        return None

    def get_security_policies(self) -> Dict[str, Any]:
        """Returns the security policies dictionary."""
        return self.security_policies

