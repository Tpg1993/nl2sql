import re
from typing import Dict, Any, Tuple
from sqlalchemy import text

class CostPlanner:
    """Performs pre-execution query cost plan analysis on SQLite and Databricks
    to audit resource usage and intercept Cartesian products or huge scans.
    """

    def __init__(self, engine) -> None:
        self.engine = engine
        self.dialect = getattr(engine, "dialect", None)
        self.dialect_name = getattr(self.dialect, "name", "sqlite")

    def analyze_query(self, sql_query: str) -> Tuple[bool, str, Dict[str, Any]]:
        """Runs EXPLAIN/EXPLAIN QUERY PLAN and evaluates cost constraints.
        Returns:
            (is_safe: bool, reason: str, plan_metrics: dict)
        """
        # Strip trailing semicolon if present
        sql_stripped = sql_query.strip().rstrip(";")
        if not sql_stripped.upper().startswith("SELECT"):
            # Non-select statements are audited by the security auditor
            return True, "Passed basic write check", {}

        try:
            if self.dialect_name == "sqlite":
                explain_sql = f"EXPLAIN QUERY PLAN {sql_stripped}"
                with self.engine.connect() as conn:
                    # SQLite returns cols: id, parent, notused, detail
                    result = conn.execute(text(explain_sql)).fetchall()
                
                details = [row[3] for row in result]
                
                # Metrics / checks
                scan_tables = []
                temp_triggers = []
                
                for d in details:
                    d_lower = d.lower()
                    if "scan" in d_lower:
                        # Find table name or alias
                        match = re.search(r"scan (?:table )?(\w+)", d_lower)
                        if match:
                            scan_tables.append(match.group(1))
                    if "temp b-tree" in d_lower or "use temp" in d_lower:
                        temp_triggers.append(d)
                
                is_safe = True
                reason = "Query plan meets safety criteria."
                
                # Rule: block queries that do Cartesian joins or scan too many tables without index (e.g. >= 4 tables)
                if len(scan_tables) >= 4:
                    is_safe = False
                    reason = f"Query involves too many full table scans ({len(scan_tables)} tables: {', '.join(scan_tables)}). Please refine your search filters."
                
                # Check for Cartesian product / cross join
                query_upper = sql_stripped.upper()
                if "CROSS JOIN" in query_upper or ("," in query_upper and "JOIN" not in query_upper and "WHERE" not in query_upper and len(scan_tables) > 1):
                    is_safe = False
                    reason = "Potential Cartesian product (cross join) detected. Please specify explicit JOIN conditions using ON syntax."
                
                metrics = {
                    "scan_tables": scan_tables,
                    "scanned_tables_count": len(scan_tables),
                    "temp_triggers_count": len(temp_triggers),
                    "details": details
                }
                
                return is_safe, reason, metrics

            else:
                # Databricks / Spark SQL dialect
                explain_sql = f"EXPLAIN {sql_stripped}"
                with self.engine.connect() as conn:
                    result = conn.execute(text(explain_sql)).fetchall()
                
                # Spark EXPLAIN typically returns a single string row
                plan_str = "\n".join([str(row[0]) for row in result])
                
                is_safe = True
                reason = "Query plan meets Spark safety criteria."
                metrics = {"plan_text": plan_str}
                
                plan_str_lower = plan_str.lower()
                
                # Spark cost/safety checks:
                if "cartesianproduct" in plan_str_lower or "cartesian product" in plan_str_lower:
                    is_safe = False
                    reason = "Cartesian Product detected in Databricks execution plan. This will result in an expensive out-of-memory scan. Please specify join keys."
                
                elif "broadcastnestedloopjoin" in plan_str_lower:
                    is_safe = False
                    reason = "Broadcast Nested Loop Join detected. This indicates an unindexed join or potential cross-join. Please optimize join keys."
                
                # Check size limits
                byte_scans = re.findall(r"sizeInBytes=([\d\.]+)\s*(\w+)", plan_str)
                for amount, unit in byte_scans:
                    amount_val = float(amount)
                    if (unit.upper() == "GB" and amount_val > 10.0) or (unit.upper() == "TB"):
                        is_safe = False
                        reason = f"Plan estimates scan size of {amount} {unit}, exceeding the 10 GB limit. Add date range or partition filters."
                        break
                
                return is_safe, reason, metrics

        except Exception as e:
            return False, f"Failed to generate cost execution plan: {str(e)}", {}
