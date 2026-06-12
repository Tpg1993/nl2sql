import re
from typing import Dict, Any, Tuple, List
from sqlalchemy import text, inspect

class CostPlanner:
    """Performs pre-execution query cost plan analysis on SQLite and Databricks
    to audit resource usage and intercept Cartesian products or huge scans.
    """

    def __init__(self, engine) -> None:
        self.engine = engine
        self.dialect = getattr(engine, "dialect", None)
        self.dialect_name = getattr(self.dialect, "name", "sqlite")
        self.table_sizes = {}
        
        # Cache table sizes (row counts) on startup for SQLite
        if self.dialect_name == "sqlite":
            try:
                with self.engine.connect() as conn:
                    inspector = inspect(self.engine)
                    tables = inspector.get_table_names()
                    for t in tables:
                        res = conn.execute(text(f"SELECT COUNT(*) FROM {t}")).fetchone()
                        self.table_sizes[t] = res[0] if res else 0
                print(f"[CostPlanner] Cached row counts for database tables: {self.table_sizes}")
            except Exception as e:
                print(f"[CostPlanner] Warning: failed to cache table sizes: {e}")

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
                
                # Build alias map to locate referenced columns/tables
                alias_map = {}
                for tbl in self.table_sizes:
                    alias_map[tbl.lower()] = tbl.lower()

                keywords = {"JOIN", "INNER", "LEFT", "RIGHT", "CROSS", "NATURAL", "OUTER", "ON", "WHERE", "GROUP", "ORDER", "LIMIT", "USING", "UNION", "SELECT", "AS"}
                matches = re.finditer(r"\b(?:FROM|JOIN)\s+(\w+)(?:\s+(?:AS\s+)?(\w+))?", sql_stripped, re.IGNORECASE)
                for match in matches:
                    tbl_name = match.group(1)
                    alias = match.group(2)
                    if tbl_name.lower() in self.table_sizes:
                        if alias and alias.upper() not in keywords:
                            alias_map[alias.lower()] = tbl_name.lower()

                # Dynamic table scan metrics and row estimation
                scan_tables = []
                temp_triggers = []
                scan_breakdown = []
                total_estimated_rows_scanned = 0
                
                for d in details:
                    d_lower = d.lower()
                    if "temp b-tree" in d_lower or "use temp" in d_lower:
                        temp_triggers.append(d)
                        
                    # Parse SCAN TABLE
                    scan_match = re.search(r"scan (?:table )?(\w+)", d_lower)
                    if scan_match:
                        tbl = scan_match.group(1)
                        resolved_tbl = alias_map.get(tbl.lower(), tbl.lower())
                        scan_tables.append(resolved_tbl)
                        
                        using_index = "using" in d_lower and "index" in d_lower
                        tbl_size = self.table_sizes.get(resolved_tbl, 0)
                        
                        if using_index:
                            # Logarithmic/index scan - relatively cheap
                            estimated_rows = min(100, tbl_size // 10) if tbl_size > 0 else 0
                            scan_type = "Index Scan (Covering)"
                        else:
                            # Full table scan - expensive!
                            estimated_rows = tbl_size
                            scan_type = "Full Table Scan"
                            
                        total_estimated_rows_scanned += estimated_rows
                        scan_breakdown.append({
                            "table": resolved_tbl,
                            "type": scan_type,
                            "estimated_rows": estimated_rows,
                            "detail": d
                        })
                        
                    # Parse SEARCH TABLE
                    search_match = re.search(r"search (?:table )?(\w+)", d_lower)
                    if search_match:
                        tbl = search_match.group(1)
                        resolved_tbl = alias_map.get(tbl.lower(), tbl.lower())
                        # Index Lookup is highly optimized - 1 row access
                        scan_breakdown.append({
                            "table": resolved_tbl,
                            "type": "Index Lookup",
                            "estimated_rows": 1,
                            "detail": d
                        })
                        total_estimated_rows_scanned += 1
                
                # Find all table.column references in the query
                column_refs = re.findall(r"\b(\w+)\.(\w+)\b", sql_stripped)
                table_columns_referenced = {}
                for prefix, col in column_refs:
                    tbl_name = alias_map.get(prefix.lower())
                    if tbl_name:
                        if tbl_name not in table_columns_referenced:
                            table_columns_referenced[tbl_name] = set()
                        table_columns_referenced[tbl_name].add(col.lower())

                # Fallback: identify columns belonging to scanned tables referenced without prefix
                for tbl in scan_tables:
                    if tbl not in table_columns_referenced:
                        table_columns_referenced[tbl] = set()
                    try:
                        inspector = inspect(self.engine)
                        cols = [c["name"].lower() for c in inspector.get_columns(tbl)]
                        for col in cols:
                            # Use word boundary checks to see if the column name appears in the query
                            if re.search(rf"\b{col}\b", sql_stripped, re.IGNORECASE):
                                table_columns_referenced[tbl].add(col)
                    except Exception as e:
                        pass
                
                # Generate Missing Index Advisories for heavy scans
                optimizer_advisories = []
                for item in scan_breakdown:
                    if item["type"] == "Full Table Scan":
                        tbl = item["table"]
                        tbl_size = self.table_sizes.get(tbl, 0)
                        
                        if tbl_size > 100:  # Only suggest indexes for tables of substantial size
                            referenced_cols = table_columns_referenced.get(tbl, set())
                            
                            try:
                                inspector = inspect(self.engine)
                                pks = inspector.get_pk_constraint(tbl).get("constrained_columns", [])
                                pks_lower = [pk.lower() for pk in pks]
                                
                                indexes = inspector.get_indexes(tbl)
                                indexed_cols = []
                                for idx in indexes:
                                    indexed_cols.extend([c.lower() for c in idx.get("column_names", []) if c])
                                    
                                for col in referenced_cols:
                                    if col not in pks_lower and col not in indexed_cols:
                                        rec = f"CREATE INDEX idx_{tbl}_{col} ON {tbl}({col});"
                                        optimizer_advisories.append({
                                            "table": tbl,
                                            "column": col,
                                            "reason": f"Table '{tbl}' undergoes a Full Table Scan while filtering/joining on column '{col}'.",
                                            "suggestion": rec
                                        })
                            except Exception as parse_err:
                                print(f"[CostPlanner] Failed to compile advisory metadata: {parse_err}")

                is_safe = True
                reason = "Query plan meets safety criteria."
                
                # Rule: block queries that scan too many rows cumulatively
                if total_estimated_rows_scanned >= 5000:
                    is_safe = False
                    reason = f"Query involves high execution cost (estimated {total_estimated_rows_scanned} rows scanned). Please refine your search filters or add query conditions."
                
                # Rule: block queries with Cartesian products / cross joins
                query_upper = sql_stripped.upper()
                if "CROSS JOIN" in query_upper or ("," in query_upper and "JOIN" not in query_upper and "WHERE" not in query_upper and len(scan_tables) > 1):
                    is_safe = False
                    reason = "Potential Cartesian product (cross join) detected. Please specify explicit JOIN conditions using ON syntax."
                
                metrics = {
                    "scan_tables": scan_tables,
                    "scanned_tables_count": len(scan_tables),
                    "temp_triggers_count": len(temp_triggers),
                    "total_estimated_rows_scanned": total_estimated_rows_scanned,
                    "scan_breakdown": scan_breakdown,
                    "optimizer_advisories": optimizer_advisories,
                    "raw_plan": "\n".join(details)
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
                metrics = {
                    "plan_text": plan_str,
                    "raw_plan": plan_str,
                    "scan_breakdown": [],
                    "optimizer_advisories": []
                }
                
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
