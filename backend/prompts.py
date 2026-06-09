import os

class PromptLibrary:
    """A centralized Prompt Library that loads template text files dynamically
    from the filesystem. This permits swapping prompts easily for other database schemas.
    """

    def __init__(self, prompts_dir: str = None) -> None:
        if prompts_dir is None:
            # Resolve prompts folder relative to this script
            BASE_DIR = os.path.dirname(os.path.abspath(__file__))
            prompts_dir = os.path.join(BASE_DIR, "prompts")
            
        self.prompts_dir = prompts_dir
        self.templates = {}
        self._load_templates()

    def _load_templates(self) -> None:
        """Reads flat text files containing placeholders and caches them on startup."""
        required_templates = [
            "sql_generation.txt",
            "sql_generation_retry.txt",
            "summarization.txt",
            "summarization_error.txt"
        ]
        
        for filename in required_templates:
            filepath = os.path.join(self.prompts_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    # Strip to normalize line endings and spaces
                    self.templates[filename] = f.read()
            except Exception as e:
                # Fallback to absolute defaults if filesystem loading fails, ensuring stability
                print(f"[PromptLibrary] Warning: Failed to load {filename} from {filepath}: {e}. Loading default string.")
                self.templates[filename] = self._get_fallback_template(filename)

    def _get_fallback_template(self, filename: str) -> str:
        """Provides hardcoded fallback string templates if text files are missing."""
        if filename == "sql_generation.txt":
            return (
                "You are an SQL database query expert. Generate the correct query matching the schema and semantic layer rules below.\n\n"
                "User Query Request: {user_question}\n\n"
                "Database Semantic Layer Context (Business logic definitions, calculations, and join keys):\n{semantic_context}\n\n"
                "Database Schema Context (Raw table schema details):\n{db_schema}\n\n"
                "Rules:\n"
                "- Use ONLY safe SELECT operations. Do not update, alter, append, or drop tables.\n"
                "- Return ONLY the clean, raw SQL string payload. Do not wrap it in markdown framing like ```sql.\n"
                "- Prioritize using the join relationships defined in the semantic layer relationships.\n"
                "- Prioritize formulas in the metrics list for calculations (e.g. active allergies calculation)."
            )
        elif filename == "sql_generation_retry.txt":
            return (
                "WARNING: The SQL query you previously generated failed with the following execution error:\n{previous_error}\n\n"
                "Analyze the error, raw schema, and semantic layer. Generate a corrected SQL query using only valid, existing column names and SQL syntax. Avoid repeating the same mistake."
            )
        elif filename == "summarization.txt":
            return (
                "You are a clinical data summarizer. Write a clean, brief natural language response answering the user's clinical question based directly on the database query results. Keep it simple and direct. Do not explain SQL syntax or mention table names.\n\n"
                "User Question: {user_question}\n"
                "Database Result: {db_result}"
            )
        elif filename == "summarization_error.txt":
            return (
                "Write a brief, polite response explaining that we couldn't resolve the database query due to an execution error.\n\n"
                "Original Question: {user_question}\n"
                "Error Details: {db_result}"
            )
        return ""

    def format_sql_generation(self, user_question: str, semantic_context: str, db_schema: str, previous_error: str = None) -> str:
        """Formats the SQL generation prompt template with parameters, injecting retry context if present."""
        base_template = self.templates.get("sql_generation.txt", "")
        prompt = base_template.format(
            user_question=user_question,
            semantic_context=semantic_context,
            db_schema=db_schema
        )
        
        if previous_error:
            retry_template = self.templates.get("sql_generation_retry.txt", "")
            formatted_retry = retry_template.format(previous_error=previous_error)
            prompt += f"\n\n{formatted_retry}"
            
        return prompt

    def format_summarization(self, user_question: str, db_result: str, error_context: bool = False) -> str:
        """Formats the clinical summarization prompt template with parameters."""
        template_name = "summarization_error.txt" if error_context else "summarization.txt"
        template = self.templates.get(template_name, "")
        
        return template.format(
            user_question=user_question,
            db_result=db_result
        )
