const API_BASE = "http://127.0.0.1:8000/api";

// DOM Elements
const statusBadge = document.getElementById("status-badge");
const statusText = document.getElementById("status-text");
const dialectContainer = document.getElementById("dialect-container");
const dialectVal = document.getElementById("dialect-val");
const schemaLoading = document.getElementById("schema-loading");
const tableListContainer = document.getElementById("table-list-container");
const chatMessages = document.getElementById("chat-messages");
const welcomeView = document.getElementById("welcome-view");
const queryForm = document.getElementById("query-form");
const userInput = document.getElementById("user-input");
const sendButton = document.getElementById("send-button");

// On Load: Fetch schema metadata
window.addEventListener("DOMContentLoaded", () => {
    fetchSchemaMetadata();
});

// Fetch Schema Details from FastAPI
async function fetchSchemaMetadata() {
    try {
        const response = await fetch(`${API_BASE}/metadata`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        
        if (data.success) {
            // Update Status Badge
            statusBadge.classList.add("connected");
            statusBadge.classList.remove("error");
            statusText.textContent = "Connected";
            
            // Update Dialect
            dialectContainer.style.display = "flex";
            dialectVal.textContent = data.dialect || "sqlite";
            
            // Populate Schema Sidebar
            renderSchema(data.schema);
        } else {
            showSchemaError(data.error || "Failed to load database schema.");
        }
    } catch (error) {
        console.error("Error fetching schema:", error);
        showSchemaError("Connection to backend API failed.");
    }
}

// Render Schema Sidebar List
function renderSchema(schema) {
    schemaLoading.style.display = "none";
    tableListContainer.innerHTML = "";
    
    if (Object.keys(schema).length === 0) {
        tableListContainer.innerHTML = `<li style="font-size: 0.8rem; color: var(--text-muted); font-style: italic;">No tables found</li>`;
        return;
    }
    
    Object.entries(schema).forEach(([tableName, columns]) => {
        const li = document.createElement("li");
        li.className = "table-item";
        
        // Header
        const header = document.createElement("div");
        header.className = "table-header";
        header.innerHTML = `
            <div class="table-name-wrapper">
                <i class="fa-solid fa-table table-icon"></i>
                <span class="schema-helper-link" title="Click to insert table name">${tableName}</span>
            </div>
            <i class="fa-solid fa-chevron-right chevron-icon"></i>
        `;
        
        // Column List Container
        const colList = document.createElement("ul");
        colList.className = "column-list";
        
        columns.forEach(col => {
            const colLi = document.createElement("li");
            colLi.className = "column-item";
            colLi.innerHTML = `<span class="schema-helper-link" title="Click to insert column name">${col}</span>`;
            
            // Column insertion helper
            colLi.querySelector(".schema-helper-link").addEventListener("click", (evt) => {
                evt.stopPropagation();
                insertSchemaTerm(col);
            });
            
            colList.appendChild(colLi);
        });
        
        // Click action: Toggle Active State
        header.addEventListener("click", () => {
            li.classList.toggle("active");
        });

        // Table insertion helper
        const helperLink = header.querySelector(".schema-helper-link");
        helperLink.addEventListener("click", (evt) => {
            evt.stopPropagation(); // Prevent toggling columns
            insertSchemaTerm(tableName);
        });
        
        li.appendChild(header);
        li.appendChild(colList);
        tableListContainer.appendChild(li);
    });
}

// Append clicked terms into the search bar
function insertSchemaTerm(term) {
    const currentText = userInput.value;
    if (!currentText) {
        userInput.value = term + " ";
    } else if (currentText.endsWith(" ")) {
        userInput.value = currentText + term + " ";
    } else {
        userInput.value = currentText + " " + term + " ";
    }
    userInput.focus();
}

// Show Error in Sidebar
function showSchemaError(message) {
    schemaLoading.style.display = "none";
    statusBadge.classList.add("error");
    statusBadge.classList.remove("connected");
    statusText.textContent = "Offline";
    tableListContainer.innerHTML = `
        <li style="font-size: 0.8rem; color: #FCA5A5; padding: 12px; background-color: var(--error-bg); border-radius: 8px; border: 1px solid rgba(239, 68, 68, 0.2)">
            <i class="fa-solid fa-circle-exclamation" style="margin-right: 6px; color: var(--error);"></i>
            ${message}
        </li>
    `;
}

// Form Submission Event
queryForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const question = userInput.value.trim();
    if (!question) return;
    
    submitQuestion(question);
});

// Click Example Questions
function submitExample(question) {
    submitQuestion(question);
}

// Core submit handling
async function submitQuestion(question) {
    // Hide welcome view if visible
    if (welcomeView) {
        welcomeView.style.display = "none";
    }
    
    // Append User Message bubble
    appendMessage(question, "user");
    userInput.value = "";
    userInput.blur();
    
    // Add Loader
    const loaderId = appendTypingIndicator();
    scrollToBottom();
    
    try {
        const response = await fetch(`${API_BASE}/query`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({ question })
        });
        
        removeTypingIndicator(loaderId);
        
        if (!response.ok) {
            throw new Error(`Server returned HTTP ${response.status}`);
        }
        
        const data = await response.json();
        if (data.success) {
            appendAssistantResponse(data.query, data.result, data.tokens, data.cached, data.summary, data.latency_ms, data.latency_breakdown, data.model, data.retries);
        } else {
            appendAssistantError(data.error || "An error occurred during query generation.");
        }
    } catch (error) {
        console.error("API Query error:", error);
        removeTypingIndicator(loaderId);
        appendAssistantError("Could not connect to the backend server. Make sure the FastAPI app is running.");
    }
    scrollToBottom();
}

// Append User Message to DOM
function appendMessage(text, sender) {
    const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const messageDiv = document.createElement("div");
    messageDiv.className = `message ${sender}`;
    
    const avatarIcon = sender === "user" ? "fa-user" : "fa-robot";
    
    messageDiv.innerHTML = `
        <div class="avatar">
            <i class="fa-solid ${avatarIcon}"></i>
        </div>
        <div class="message-content">
            <div class="bubble">${text}</div>
            <span class="msg-time">${time}</span>
        </div>
    `;
    
    chatMessages.appendChild(messageDiv);
}

// Add Loading Typing Indicator bubble
function appendTypingIndicator() {
    const id = "loader-" + Date.now();
    const indicatorDiv = document.createElement("div");
    indicatorDiv.className = "message assistant";
    indicatorDiv.id = id;
    
    indicatorDiv.innerHTML = `
        <div class="avatar">
            <i class="fa-solid fa-robot"></i>
        </div>
        <div class="message-content">
            <div class="typing-bubble">
                <span class="typing-dot"></span>
                <span class="typing-dot"></span>
                <span class="typing-dot"></span>
            </div>
        </div>
    `;
    chatMessages.appendChild(indicatorDiv);
    return id;
}

// Remove Typing Indicator bubble
function removeTypingIndicator(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

// Helper to extract clean header names from SQL select
function extractHeaders(sql) {
    if (!sql) return [];
    
    // Clean query newlines and tabs
    const cleaned = sql.replace(/\s+/g, ' ');
    
    // Match anything between SELECT and FROM
    const selectMatch = cleaned.match(/select\s+(.*?)\s+from/i);
    if (!selectMatch) return [];
    
    const selectClause = selectMatch[1];
    const headers = [];
    let current = "";
    let parenDepth = 0;
    
    // Parse commas, keeping function calls (e.g. COUNT(*)) intact
    for (let i = 0; i < selectClause.length; i++) {
        const char = selectClause[i];
        if (char === '(') parenDepth++;
        else if (char === ')') parenDepth--;
        
        if (char === ',' && parenDepth === 0) {
            headers.push(current.trim());
            current = "";
        } else {
            current += char;
        }
    }
    headers.push(current.trim());
    
    return headers.map(item => {
        // Resolve AS alias or field name
        const aliasMatch = item.match(/\s+as\s+(\w+)/i) || item.match(/\s+(\w+)$/);
        if (aliasMatch) {
            return aliasMatch[1].replace(/['"`]/g, '').trim().toUpperCase();
        }
        return item.replace(/['"`]/g, '').trim().toUpperCase();
    });
}

// Custom Regex SQL syntax highlighter
function highlightSQL(sql) {
    if (!sql) return "";
    
    // Escape HTML special characters
    let html = sql
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
    
    // Placeholders for strings to prevent double formatting
    const strings = [];
    html = html.replace(/('(?:''|[^'])*')/g, (match) => {
        strings.push(match);
        return `__SQL_STRING_${strings.length - 1}__`;
    });
    
    // Placeholders for numbers
    const numbers = [];
    html = html.replace(/\b(\d+)\b/g, (match) => {
        numbers.push(match);
        return `__SQL_NUMBER_${numbers.length - 1}__`;
    });
    
    // Highlight Keywords
    const keywords = [
        "SELECT", "FROM", "WHERE", "JOIN", "ON", "GROUP BY", "ORDER BY", "LIMIT",
        "AND", "OR", "COUNT", "AVG", "SUM", "MIN", "MAX", "AS", "DISTINCT", "BY", 
        "LEFT", "RIGHT", "INNER", "OUTER", "HAVING", "LIKE", "IN", "IS", "NULL", "NOT"
    ];
    
    keywords.forEach(kw => {
        const regex = new RegExp(`\\b(${kw})\\b`, "gi");
        html = html.replace(regex, '<span class="sql-keyword">$1</span>');
    });
    
    // Restore numbers with styling
    numbers.forEach((num, idx) => {
        html = html.replace(`__SQL_NUMBER_${idx}__`, `<span class="sql-number">${num}</span>`);
    });
    
    // Restore strings with styling
    strings.forEach((str, idx) => {
        html = html.replace(`__SQL_STRING_${idx}__`, `<span class="sql-string">${str}</span>`);
    });
    
    return html;
}

// Append Assistant Success Response bubble (SQL, Table, and Tokens)
function appendAssistantResponse(sqlQuery, queryResult, tokens, cached = false, summary = "", latencyMs = null, latencyBreakdown = null, model = "gpt-4o-mini", retries = 0) {
    const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const messageDiv = document.createElement("div");
    messageDiv.className = "message assistant";
    
    // Guess header column names from the query SQL
    const headers = extractHeaders(sqlQuery);
    
    // Create Table HTML
    let tableHtml = "";
    
    if (!queryResult || queryResult.length === 0 || (Array.isArray(queryResult) && queryResult.length === 0)) {
        tableHtml = `<div class="empty-results">No records found or empty query output.</div>`;
    } else if (typeof queryResult === "string") {
        // It's a raw string
        tableHtml = `<div class="empty-results">${queryResult}</div>`;
    } else if (Array.isArray(queryResult)) {
        // List of tuples/lists or flat lists
        tableHtml = `<table class="result-table"><thead><tr>`;
        
        // Generate Headers dynamically
        const firstRow = queryResult[0];
        const colCount = Array.isArray(firstRow) ? firstRow.length : (typeof firstRow === 'object' && firstRow !== null ? Object.keys(firstRow).length : 1);
        
        for (let idx = 0; idx < colCount; idx++) {
            const hName = headers[idx] || `COLUMN_${idx + 1}`;
            tableHtml += `<th>${hName}</th>`;
        }
        tableHtml += `</tr></thead><tbody>`;
        
        // Populate Rows
        queryResult.forEach(row => {
            tableHtml += `<tr>`;
            if (Array.isArray(row)) {
                row.forEach(cell => {
                    tableHtml += `<td>${cell === null ? 'NULL' : cell}</td>`;
                });
            } else if (typeof row === 'object' && row !== null) {
                Object.values(row).forEach(cell => {
                    tableHtml += `<td>${cell === null ? 'NULL' : cell}</td>`;
                });
            } else {
                tableHtml += `<td>${row === null ? 'NULL' : row}</td>`;
            }
            tableHtml += `</tr>`;
        });
        
        tableHtml += `</tbody></table>`;
    }
    
    // Copy button handler logic
    const copyId = "copy-" + Date.now();
    
    messageDiv.innerHTML = `
        <div class="avatar">
            <i class="fa-solid fa-robot"></i>
        </div>
        <div class="message-content" style="flex: 1;">
            <div class="bubble" style="width: 100%;">
                <!-- Conversational Summary response -->
                ${summary ? `<div class="assistant-summary">${summary}</div>` : `<p>The query compiled and executed successfully. Here are the details:</p>`}
                
                <!-- SQL Statement Card -->
                <div class="sql-panel">
                    <div class="sql-panel-header">
                        <span><i class="fa-solid fa-code"></i>Generated SQL Statement</span>
                        <button class="copy-btn" id="${copyId}">
                            <i class="fa-regular fa-copy"></i> Copy
                        </button>
                    </div>
                    <pre><code>${highlightSQL(sqlQuery)}</code></pre>
                </div>
                
                <!-- Query Result Table -->
                <div class="result-table-wrapper">
                    ${tableHtml}
                </div>
                
                <!-- Performance Metadata (Always Visible) -->
                <div class="query-meta-bar">
                    <span class="meta-item">
                        <i class="fa-regular fa-clock"></i> 
                        Latency: <strong>${latencyMs !== null ? parseFloat(latencyMs).toFixed(2) + ' ms' : 'N/A'}</strong>
                        <span class="info-tooltip" data-tooltip="Total round-trip latency. On cache miss, this includes schema reflection, LLM query generation, database execution, and result summarization. On cache hit, this measures fast cache retrieval.">
                            <i class="fa-solid fa-circle-info"></i>
                        </span>
                    </span>
                    <span class="meta-item">
                        <i class="fa-solid fa-microchip"></i> 
                        Model: <strong>${model}</strong>
                    </span>
                    ${retries > 0 
                        ? `<span class="meta-item retry-badge"><i class="fa-solid fa-arrows-spin"></i> Retries: <strong>${retries}</strong> (Self-Healed)</span>`
                        : `<span class="meta-item"><i class="fa-solid fa-check-double"></i> Retries: <strong>0</strong></span>`
                    }
                    ${cached ? `<span class="meta-item cache-badge"><i class="fa-solid fa-cloud-bolt"></i> Served from Cache</span>` : ''}
                </div>
                
                <!-- Token Usage & Latency Details -->
                ${tokens ? `
                <div class="token-details-container ${cached ? 'cached-hit' : ''}">
                    <details class="token-details" ${cached ? 'open' : ''}>
                        <summary>
                            <span class="summary-title">
                                ${cached 
                                    ? `<i class="fa-solid fa-cloud-bolt"></i> Token & Latency Details (Cache Hit!)` 
                                    : `<i class="fa-solid fa-bolt"></i> Token & Latency Details`
                                }
                            </span>
                            <i class="fa-solid fa-chevron-down summary-arrow"></i>
                        </summary>
                        <div class="token-stats">
                            <div class="stat-item">
                                <span class="stat-label">${cached ? 'Saved Input' : 'Input Tokens'}</span>
                                <span class="stat-value ${cached ? 'saved-highlight' : ''}">${tokens.input || '0'}</span>
                            </div>
                            <div class="stat-item">
                                <span class="stat-label">${cached ? 'Saved Output' : 'Output Tokens'}</span>
                                <span class="stat-value ${cached ? 'saved-highlight' : ''}">${tokens.output || '0'}</span>
                            </div>
                            <div class="stat-item total ${cached ? 'saved' : ''}">
                                <span class="stat-label">${cached ? 'Total Saved' : 'Total Tokens'}</span>
                                <span class="stat-value">${tokens.total || '0'}</span>
                            </div>
                        </div>
                        
                        <div class="latency-info-row">
                            <i class="fa-regular fa-clock"></i>
                            <span>
                                Execution Latency: <strong>${latencyMs !== null ? parseFloat(latencyMs).toFixed(2) + ' ms' : 'N/A'}</strong>
                                <span class="info-tooltip" data-tooltip="Total round-trip latency. On cache miss, this includes schema reflection, LLM query generation, database execution, and result summarization. On cache hit, this measures fast cache retrieval.">
                                    <i class="fa-solid fa-circle-info"></i>
                                </span>
                            </span>
                        </div>

                        ${latencyBreakdown ? `
                        <div class="latency-breakdown-panel">
                            <h4>Stage Latency Breakdown</h4>
                            <div class="latency-stages-grid">
                                ${Object.entries(latencyBreakdown).map(([stage, ms]) => {
                                    if (ms === 0 && stage === 'cache') return '';
                                    if (ms === 0 && cached) return ''; // Hide db/llm breakdown on cache hit
                                    
                                    const labels = {
                                        cache: "Cache Retrieval",
                                        schema: "Schema Reflection",
                                        generation: "SQL Translation (LLM)",
                                        execution: "DB Query Execution",
                                        summarization: "Result Summarization"
                                    };
                                    const tooltips = {
                                        cache: "Time spent retrieving serialized responses from Redis or SQLite cache.",
                                        schema: "Time spent reflecting database tables and column schemas.",
                                        generation: "Time spent by LLM generating clean SQL queries from clinical inputs.",
                                        execution: "Time spent executing the generated query against SQLite/Databricks database engines.",
                                        summarization: "Time spent by LLM parsing query outcomes into a conversational narrative."
                                    };
                                    
                                    const label = labels[stage] || stage;
                                    const tooltip = tooltips[stage] || "";
                                    
                                    return `
                                    <div class="latency-stage-item">
                                        <span class="stage-label">
                                            ${label}
                                            <span class="info-tooltip" data-tooltip="${tooltip}">
                                                <i class="fa-solid fa-circle-info"></i>
                                            </span>
                                        </span>
                                        <span class="stage-value">${parseFloat(ms).toFixed(2)} ms</span>
                                    </div>
                                    `;
                                }).join('')}
                            </div>
                        </div>
                        ` : ''}

                        <div class="agent-metadata-panel">
                            <h4>Agent Execution Context</h4>
                            <div class="agent-meta-grid">
                                <div class="meta-detail-item">
                                    <span class="detail-label">AI Model Engine</span>
                                    <span class="detail-value"><strong>${model}</strong></span>
                                </div>
                                <div class="meta-detail-item">
                                    <span class="detail-label">Self-Healing Retries</span>
                                    <span class="detail-value ${retries > 0 ? 'healed-text' : ''}">
                                        <strong>${retries}</strong> ${retries > 0 ? '(Self-Healed SQL Syntax)' : '(Direct Compilation)'}
                                    </span>
                                </div>
                            </div>
                        </div>

                        ${cached ? `
                        <div class="cache-saving-banner">
                            <i class="fa-solid fa-piggy-bank"></i>
                            <span>Cost Saved: <strong>100%</strong> (Fast response served from cache)</span>
                        </div>
                        ` : ''}
                    </details>
                </div>
                ` : ''}
            </div>
            <span class="msg-time">${time}</span>
        </div>
    `;
    
    chatMessages.appendChild(messageDiv);
    
    // Attach copy button action
    document.getElementById(copyId).addEventListener("click", () => {
        navigator.clipboard.writeText(sqlQuery).then(() => {
            const btn = document.getElementById(copyId);
            btn.innerHTML = `<i class="fa-solid fa-check" style="color: var(--success);"></i> Copied!`;
            setTimeout(() => {
                btn.innerHTML = `<i class="fa-regular fa-copy"></i> Copy`;
            }, 2000);
        });
    });
}

// Append Assistant Error Bubble
function appendAssistantError(errorMsg) {
    const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const messageDiv = document.createElement("div");
    messageDiv.className = "message assistant";
    
    messageDiv.innerHTML = `
        <div class="avatar">
            <i class="fa-solid fa-robot"></i>
        </div>
        <div class="message-content">
            <div class="bubble">
                <p>I encountered an error while executing your request.</p>
                <div class="error-bubble">
                    <i class="fa-solid fa-triangle-exclamation"></i>
                    <span>${errorMsg}</span>
                </div>
            </div>
            <span class="msg-time">${time}</span>
        </div>
    `;
    chatMessages.appendChild(messageDiv);
}

// Scroll to bottom of chat container
function scrollToBottom() {
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

// =====================================================================
// THEME SWITCHER LOGIC
// =====================================================================
const themeToggleBtn = document.getElementById("theme-toggle-btn");
const savedTheme = localStorage.getItem("theme") || "dark";

// Apply current stored theme
if (savedTheme === "light") {
    document.body.setAttribute("data-theme", "light");
    updateThemeIcon("light");
} else {
    document.body.setAttribute("data-theme", "dark");
    updateThemeIcon("dark");
}

// Toggle click handler
themeToggleBtn.addEventListener("click", () => {
    const currentTheme = document.body.getAttribute("data-theme");
    const nextTheme = currentTheme === "light" ? "dark" : "light";
    
    document.body.setAttribute("data-theme", nextTheme);
    localStorage.setItem("theme", nextTheme);
    updateThemeIcon(nextTheme);
});

function updateThemeIcon(theme) {
    const icon = themeToggleBtn.querySelector("i");
    if (theme === "light") {
        icon.className = "fa-solid fa-moon";
    } else {
        icon.className = "fa-solid fa-sun";
    }
}
