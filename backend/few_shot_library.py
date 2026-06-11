import os
import re
import math
import yaml
from typing import List, Dict, Any

class TFIDFSimilarityMatcher:
    """A local term frequency-inverse document frequency matcher for questions."""
    def __init__(self, examples: List[Dict[str, Any]], stem_fn, tokenize_fn) -> None:
        self.examples = examples
        self._stem = stem_fn
        self._tokenize = tokenize_fn
        self.index = self._build_index()

    def _build_index(self) -> Dict[str, Any]:
        doc_info = {}
        for idx, ex in enumerate(self.examples):
            question = ex.get("question", "")
            tokens = self._tokenize(question)
            tf = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1
            doc_info[idx] = {
                "tokens": tokens,
                "tf": tf,
                "length": len(tokens)
            }
        
        # Calculate IDF
        num_docs = len(self.examples)
        df = {}
        for doc in doc_info.values():
            unique_tokens = set(doc["tf"].keys())
            for t in unique_tokens:
                df[t] = df.get(t, 0) + 1
        
        idf = {}
        for token, count in df.items():
            idf[token] = math.log((num_docs + 1) / (count + 1)) + 1
            
        return {
            "docs": doc_info,
            "idf": idf
        }

    def match(self, question: str, top_k: int = 2) -> List[Dict[str, Any]]:
        q_tokens = self._tokenize(question)
        if not q_tokens:
            return [{**ex, "score": 0.0} for ex in self.examples[:top_k]]
            
        q_tf = {}
        for t in q_tokens:
            q_tf[t] = q_tf.get(t, 0) + 1
            
        q_tfidf = {}
        for t, tf_val in q_tf.items():
            if t in self.index["idf"]:
                q_tfidf[t] = tf_val * self.index["idf"][t]
                
        q_mag = math.sqrt(sum(v ** 2 for v in q_tfidf.values()))
        if q_mag == 0:
            return [{**ex, "score": 0.0} for ex in self.examples[:top_k]]
            
        scores = []
        for idx, ex in enumerate(self.examples):
            doc = self.index["docs"][idx]
            dot_product = 0.0
            doc_tfidf = {}
            for t, tf_val in doc["tf"].items():
                doc_tfidf[t] = tf_val * self.index["idf"].get(t, 1.0)
            doc_mag = math.sqrt(sum(v ** 2 for v in doc_tfidf.values()))
            
            for t in q_tfidf:
                if t in doc_tfidf:
                    dot_product += q_tfidf[t] * doc_tfidf[t]
                    
            score = 0.0
            if doc_mag > 0:
                score = dot_product / (q_mag * doc_mag)
            scores.append((ex, score))
            
        # Sort by score descending
        scores.sort(key=lambda item: item[1], reverse=True)
        results = []
        for ex, score in scores[:top_k]:
            results.append({**ex, "score": float(round(score, 4))})
        return results

class FewShotLibrary:
    """Manages the registration and similarity matching of few-shot natural language
    to SQL query translation examples.
    """
    def __init__(self, semantic_layer_path: str = None) -> None:
        if semantic_layer_path is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            semantic_layer_path = os.path.join(base_dir, "semantic_layer.yaml")

        self.semantic_layer_path = semantic_layer_path
        self.examples = []
        self.embeddings = None
        self.example_embeddings = []
        
        self.load_examples()
        self.tfidf_matcher = TFIDFSimilarityMatcher(self.examples, self._stem, self._tokenize)
        self.init_embeddings()

    def load_examples(self) -> None:
        """Loads target query translation mappings from semantic_layer.yaml."""
        if os.path.exists(self.semantic_layer_path):
            try:
                with open(self.semantic_layer_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                self.examples = data.get("few_shot_examples", [])
                print(f"[FewShotLibrary] Loaded {len(self.examples)} examples from configuration.")
            except Exception as e:
                print(f"[FewShotLibrary] Warning: Failed to load examples from {self.semantic_layer_path}: {e}")
        else:
            print(f"[FewShotLibrary] Warning: Configuration file not found at {self.semantic_layer_path}")

    def init_embeddings(self) -> None:
        """Initializes LangChain OpenAIEmbeddings and pre-calculates example embeddings."""
        if not self.examples:
            return
            
        try:
            # We attempt to load embeddings if the API key is present
            openai_key = os.environ.get("OPENAI_API_KEY")
            if openai_key:
                from langchain_openai import OpenAIEmbeddings
                self.embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
                
                # Precompute example question embeddings for fast online retrieval
                texts = [ex.get("question", "") for ex in self.examples]
                embs = self.embeddings.embed_documents(texts)
                
                self.example_embeddings = []
                for ex, emb in zip(self.examples, embs):
                    self.example_embeddings.append((ex, emb))
                print(f"[FewShotLibrary] Precomputed embeddings for {len(self.example_embeddings)} examples.")
            else:
                print("[FewShotLibrary] No OPENAI_API_KEY found. Defaulting to local TF-IDF Cosine similarity.")
        except Exception as e:
            print(f"[FewShotLibrary] Warning: Embeddings initialization failed: {e}. Defaulting to TF-IDF.")
            self.embeddings = None
            self.example_embeddings = []

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
        words = re.findall(r"\b\w+\b", text.lower())
        stopwords = {
            "a", "an", "the", "and", "or", "but", "in", "on", "at", "for", "with", 
            "of", "about", "to", "from", "by", "is", "are", "was", "were", "be", 
            "been", "being", "have", "has", "had", "do", "does", "did"
        }
        raw_tokens = [w for w in words if len(w) > 1 and w not in stopwords]
        return [self._stem(token) for token in raw_tokens]

    def retrieve_few_shots(self, question: str, top_k: int = 2) -> List[Dict[str, Any]]:
        """Retrieves top_k relevant few-shot examples using embedding distance or TF-IDF fallback."""
        if not self.examples:
            return []
            
        # Try Embedding similarity match first
        if self.embeddings and self.example_embeddings:
            try:
                q_emb = self.embeddings.embed_query(question)
                scores = []
                for ex, emb in self.example_embeddings:
                    # Cosine similarity calculation
                    dot = sum(a * b for a, b in zip(q_emb, emb))
                    mag_a = math.sqrt(sum(a ** 2 for a in q_emb))
                    mag_b = math.sqrt(sum(b ** 2 for b in emb))
                    sim = dot / (mag_a * mag_b) if mag_a > 0 and mag_b > 0 else 0.0
                    scores.append((ex, sim))
                scores.sort(key=lambda item: item[1], reverse=True)
                return [{**ex, "score": float(round(score, 4))} for ex, score in scores[:top_k]]
            except Exception as e:
                print(f"[FewShotLibrary] Warning: Embedding retrieval failed: {e}. Falling back to TF-IDF.")
                
        # TF-IDF Cosine Similarity Fallback
        return self.tfidf_matcher.match(question, top_k=top_k)
