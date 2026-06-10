import os
import re
import math
import yaml
from typing import List, Dict, Any
from sqlalchemy import inspect

class MetadataRAG:
    def __init__(self, db_engine, semantic_layer_path: str = None) -> None:
        self.engine = db_engine
        
        # Load semantic layer if path is not provided
        if semantic_layer_path is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            semantic_layer_path = os.path.join(base_dir, "semantic_layer.yaml")
            
        self.semantic_data = {}
        if os.path.exists(semantic_layer_path):
            try:
                with open(semantic_layer_path, "r") as f:
                    self.semantic_data = yaml.safe_load(f) or {}
            except Exception as e:
                print(f"[MetadataRAG] Warning: failed to load semantic layer yaml: {e}")
                
        self.index = self._build_index()

    def _stem(self, word: str) -> str:
        word = word.lower()
        if word.endswith("ies"):
            return word[:-3] + "y"
        if word.endswith("sses"):
            return word[:-2]
        if word.endswith("ses"):
            return word[:-2] + "is"
        if word.endswith("s") and not word.endswith("ss") and not word.endswith("us") and len(word) > 3:
            return word[:-1]
        return word

    def _tokenize(self, text: str) -> List[str]:
        # Split on non-alphanumeric characters, convert to lowercase
        words = re.findall(r"\b\w+\b", text.lower())
        stopwords = {
            "a", "an", "the", "and", "or", "but", "in", "on", "at", "for", "with", 
            "of", "about", "to", "from", "by", "is", "are", "was", "were", "be", 
            "been", "being", "have", "has", "had", "do", "does", "did"
        }
        raw_tokens = [w for w in words if len(w) > 1 and w not in stopwords]
        # Apply custom stemming for singular/plural compatibility
        return [self._stem(token) for token in raw_tokens]

    def _build_index(self) -> Dict[str, Dict[str, Any]]:
        # Retrieve all tables and columns using SQLAlchemy reflection
        inspector = inspect(self.engine)
        table_names = inspector.get_table_names()
        
        table_docs = {}
        
        entities = self.semantic_data.get("entities", {})
        metrics = self.semantic_data.get("metrics", {})
        relationships = self.semantic_data.get("relationships", [])
        
        for table in table_names:
            terms = [table]
            
            # Fetch columns for this table
            cols = inspector.get_columns(table)
            col_names = [col["name"] for col in cols]
            terms.extend(col_names)
            
            # Add entity details from semantic layer matching table_name
            entity_found = None
            for ent_name, ent_val in entities.items():
                if ent_val.get("table_name") == table:
                    entity_found = ent_name
                    desc = ent_val.get("description", "")
                    terms.append(desc)
                    # Add fields keys and values
                    fields = ent_val.get("fields", {})
                    for field_key, field_val in fields.items():
                        terms.append(field_key)
                        if isinstance(field_val, str):
                            terms.append(field_val)
                        elif isinstance(field_val, dict):
                            terms.append(field_val.get("column_name", ""))
                            terms.append(field_val.get("classification", ""))
                    break
            
            # Add metrics referencing this table in formula/description
            for metric_name, metric_val in metrics.items():
                formula = metric_val.get("formula", "").lower()
                desc = metric_val.get("description", "")
                if f"from {table}" in formula or f"join {table}" in formula or table in formula:
                    terms.append(metric_name)
                    terms.append(desc)
            
            # Add relationship joins mapping to this table
            for rel in relationships:
                from_tbl = entities.get(rel.get("from_entity"), {}).get("table_name", "").lower()
                to_tbl = entities.get(rel.get("to_entity"), {}).get("table_name", "").lower()
                if from_tbl == table or to_tbl == table:
                    terms.append(f"joins with {from_tbl if from_tbl != table else to_tbl}")
            
            # Combine all strings, tokenize, and calculate Term Frequencies
            full_text = " ".join(terms)
            tokens = self._tokenize(full_text)
            
            tf = {}
            for token in tokens:
                tf[token] = tf.get(token, 0) + 1
                
            table_docs[table] = {
                "tokens": tokens,
                "tf": tf,
                "length": len(tokens)
            }
            
        # Compute Inverse Document Frequencies (IDF)
        num_docs = len(table_docs)
        df = {}
        for doc_info in table_docs.values():
            unique_tokens = set(doc_info["tf"].keys())
            for token in unique_tokens:
                df[token] = df.get(token, 0) + 1
                
        idf = {}
        for token, count in df.items():
            idf[token] = math.log((num_docs + 1) / (count + 1)) + 1
            
        return {
            "docs": table_docs,
            "idf": idf
        }

    def retrieve_tables(self, question: str, top_k: int = 3) -> List[str]:
        q_tokens = self._tokenize(question)
        if not q_tokens:
            # Fallback to alphabetically sorted table subset
            return sorted(list(self.index["docs"].keys()))[:top_k]
            
        # Compute TF-IDF for query
        q_tf = {}
        for token in q_tokens:
            q_tf[token] = q_tf.get(token, 0) + 1
            
        q_tfidf = {}
        for token, tf_val in q_tf.items():
            if token in self.index["idf"]:
                q_tfidf[token] = tf_val * self.index["idf"][token]
                
        q_mag = math.sqrt(sum(val ** 2 for val in q_tfidf.values()))
        if q_mag == 0:
            return sorted(list(self.index["docs"].keys()))[:top_k]
            
        # Calculate base cosine similarity with each table document
        base_scores = {}
        for table, doc in self.index["docs"].items():
            dot_product = 0
            doc_tfidf = {}
            
            for token, tf_val in doc["tf"].items():
                doc_tfidf[token] = tf_val * self.index["idf"].get(token, 1.0)
                
            doc_mag = math.sqrt(sum(val ** 2 for val in doc_tfidf.values()))
            
            for token in q_tfidf:
                if token in doc_tfidf:
                    dot_product += q_tfidf[token] * doc_tfidf[token]
                    
            if doc_mag > 0:
                base_scores[table] = dot_product / (q_mag * doc_mag)
            else:
                base_scores[table] = 0.0

        # Apply Relational Semantic Expansion / Graph-based Metadata Boosting
        scores = base_scores.copy()
        entities = self.semantic_data.get("entities", {})
        relationships = self.semantic_data.get("relationships", [])
        
        for rel in relationships:
            from_tbl = entities.get(rel.get("from_entity"), {}).get("table_name", "")
            to_tbl = entities.get(rel.get("to_entity"), {}).get("table_name", "")
            if from_tbl in base_scores and to_tbl in base_scores:
                # Boost parent/child context if either matches
                if base_scores[from_tbl] > 0.0:
                    scores[to_tbl] = max(scores[to_tbl], base_scores[to_tbl] + 0.5 * base_scores[from_tbl])
                if base_scores[to_tbl] > 0.0:
                    scores[from_tbl] = max(scores[from_tbl], base_scores[from_tbl] + 0.5 * base_scores[to_tbl])

        # Sort tables by boosted score descending
        sorted_tables = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        print(f"[MetadataRAG] Cosine matching scores (with boosting) for '{question}': {sorted_tables}")
        
        # Pick top_k tables
        retrieved = []
        for table, score in sorted_tables:
            if len(retrieved) < top_k or score > 0.0:
                retrieved.append(table)
            if len(retrieved) >= top_k:
                break
                
        # Pad with alphabetical or highest scoring if not reached top_k
        if len(retrieved) < top_k:
            for table, _ in sorted_tables:
                if table not in retrieved:
                    retrieved.append(table)
                if len(retrieved) >= top_k:
                    break
                    
        return retrieved
