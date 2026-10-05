
import json
import os
import re
from decimal import Decimal
import streamlit as st

import mysql.connector
import requests
from mysql.connector import Error


OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5-coder:7b"
DB_NAME = "test"
TABLE_NAME = "customer_shopping"

ALLOWED_TABLES = {TABLE_NAME}

_categorical_values = None
_schema = None


# ============================================================
# MYSQL CONFIGURATION
# ============================================================

def get_mysql_config():
    return {
        "host": st.secrets["mysql"]["host"],
        "user": st.secrets["mysql"]["user"],
        "password": st.secrets["mysql"]["password"],
        "database": st.secrets["mysql"]["database"]
    }


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def initialize_database():
    """Connect to MySQL and load schema/categorical metadata once."""

    global _categorical_values, _schema, _db_password

    if _categorical_values is not None and _schema is not None:
        return

    config = get_mysql_config()

    conn = mysql.connector.connect(**config)

    if not conn.is_connected():
        raise ConnectionError("Could not connect to MySQL.")

    cursor = conn.cursor()

    cursor.execute(f"DESCRIBE {TABLE_NAME}")
    columns = cursor.fetchall()

    _schema = "\n".join(
        f"- {column[0]} ({column[1]})"
        for column in columns
    )

    categorical_columns = [
        "gender",
        "category",
        "location",
        "size",
        "season",
        "subscription_status",
        "shipping_type",
        "discount_applied",
        "payment_method",
        "frequency_of_purchases",
        "age_group"
    ]

    _categorical_values = {}

    for column in categorical_columns:
        cursor.execute(
            f"SELECT DISTINCT `{column}` FROM {TABLE_NAME}"
        )
        _categorical_values[column] = [
            row[0] for row in cursor.fetchall()
        ]

    cursor.close()
    conn.close()


def get_connection():
    """Create a MySQL connection for one analysis request."""

    global _db_password

    config = get_mysql_config()

    conn = mysql.connector.connect(**config)

    if not conn.is_connected():
        raise ConnectionError("Could not connect to MySQL.")

    return conn


# ============================================================
# OLLAMA
# ============================================================

def call_ollama(prompt, temperature=0, timeout=60):
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature
            }
        },
        timeout=timeout
    )

    response.raise_for_status()

    data = response.json()

    raw = data.get("response", "").strip()

    if not raw:
        raise RuntimeError("Ollama returned an empty response.")

    return raw


def clean_json_response(raw_response):
    raw_response = raw_response.strip()

    if raw_response.startswith("```"):
        raw_response = raw_response.replace("```json", "", 1)
        raw_response = raw_response.replace("```", "", 1)
        raw_response = raw_response.strip()

    return raw_response


# ============================================================
# CONVERSATION CONTEXT
# ============================================================

def resolve_context(question, conversation_history):
    question = question.strip()

    if not conversation_history:
        return question

    previous = conversation_history[-1]

    previous_question = previous.get(
        "standalone_question",
        previous.get("question", "")
    )

    context_prompt = f"""
You are a conversational AI data analyst.

Your ONLY job is to rewrite the CURRENT USER QUESTION
into one complete standalone data question.

Do NOT answer the question.
Do NOT generate SQL.
Do NOT explain anything.

PREVIOUS STANDALONE QUESTION:
{previous_question}

CURRENT USER QUESTION:
{question}

RULES:
1. The previous standalone question is the main context.
2. If the current question is standalone, return it unchanged.
3. If it is a follow-up, preserve intent, metric, target,
   and filters unless explicitly changed.
4. If the user says "What about...", change only the
   explicitly changed condition.
5. If the user says "How many are there?", preserve the
   subject and filters.
6. The most recent question always has priority.
7. Do not go back to older questions when the previous
   standalone question already contains the needed context.
8. Preserve negative conditions such as "not subscribed",
   "No", "isn't", and "aren't".
9. Do not invent new conditions.
10. Return ONLY the standalone question.

STANDALONE QUESTION:
"""

    try:
        raw = call_ollama(context_prompt, temperature=0)
        return raw.replace("```", "").strip().strip('"').strip("'")
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            "Could not connect to Ollama. Make sure Ollama is running."
        )
    except requests.exceptions.Timeout:
        raise RuntimeError("Context resolution took too long.")
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Context resolver error: {exc}")


# ============================================================
# SQL SAFETY VALIDATION
# ============================================================

def extract_cte_names(sql):
    """Return CTE aliases so they are not mistaken for real tables."""
    pattern = r"(?:\bWITH\s+(?:RECURSIVE\s+)?|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s+AS\s*\("
    return {name.lower() for name in re.findall(pattern, sql, flags=re.IGNORECASE)}


def extract_table_names(sql):
    pattern = r"\b(?:FROM|JOIN)\s+([a-zA-Z0-9_]+)"
    return re.findall(pattern, sql, flags=re.IGNORECASE)


def validate_sql(sql):
    """Validate that generated SQL is a single safe SELECT/CTE query."""
    sql_clean = sql.strip()

    if not sql_clean:
        return False, "SQL query is empty."

    if sql_clean.endswith(";"):
        sql_clean = sql_clean[:-1].strip()

    if ";" in sql_clean:
        return False, "Multiple SQL statements are not allowed."

    sql_lower = sql_clean.lower()

    # MySQL analytical queries may start with SELECT or WITH/CTE.
    if not (sql_lower.startswith("select") or sql_lower.startswith("with")):
        return False, "Only SELECT or CTE-based SELECT queries are allowed."

    forbidden = [
        "insert",
        "update",
        "delete",
        "drop",
        "alter",
        "truncate",
        "create",
        "replace",
        "grant",
        "revoke"
    ]

    for keyword in forbidden:
        if re.search(rf"\b{keyword}\b", sql_lower):
            return False, (
                f"Forbidden SQL operation detected: {keyword.upper()}"
            )

    if sql_lower.startswith("with") and not re.search(r"\bselect\b", sql_lower):
        return False, "A CTE query must contain a SELECT statement."

    cte_names = extract_cte_names(sql_clean)
    tables = extract_table_names(sql_clean)

    for table in tables:
        if table.lower() not in ALLOWED_TABLES and table.lower() not in cte_names:
            return False, f"Access to table '{table}' is not allowed."

    return True, "SQL is safe to execute."


# ============================================================
# SEMANTIC SQL VALIDATION
# ============================================================

def validate_sql_semantics(sql, question):
    """
    Lightweight semantic checks for common AI-generated SQL mistakes.
    This is a guardrail, not a mathematical proof of correctness.
    """

    sql_lower = sql.lower()
    sql_check = re.sub(r"'(?:''|[^'])*'", "''", sql_lower)

    warnings = []

    # MAX/MIN associated-row mistake
    if re.search(r"\b(max|min)\s*\(", sql_check):
        if "group by category" in sql_check:
            if (
                "customer_id" in sql_check
                and " join " not in sql_check
                and sql_check.count("select") <= 1
            ):
                warnings.append(
                    "MAX/MIN is being used with customer_id in a "
                    "category GROUP BY without connecting the row "
                    "back to the aggregate."
                )

    # Category-level window-function mistake
    if re.search(
        r"\bavg\s*\([^)]*\)\s+over\s*\(\s*partition\s+by\s+customer_id",
        sql_check
    ):
        if "category" in sql_check:
            warnings.append(
                "A category-level average is partitioned by "
                "customer_id instead of category."
            )

    # Tie-safe highest-row logic
    # ROW_NUMBER() keeps only one row when maximum values are tied.
    # If the user explicitly asks for all tied highest rows, require
    # RANK(), DENSE_RANK(), or MAX()+JOIN logic instead.
    question_lower = question.lower()

    asks_for_all_highest_ties = (
        "including ties" in question_lower
        or ("all customers" in question_lower and "highest" in question_lower)
        or ("all customers" in question_lower and "maximum" in question_lower)
    )

    if asks_for_all_highest_ties and "row_number()" in sql_check:
        warnings.append(
            "ROW_NUMBER() keeps only one row when highest values are tied. "
            "The question requires all tied highest rows; use RANK(), "
            "DENSE_RANK(), or MAX() with a JOIN."
        )

    # Row-vs-group aggregate comparison

    asks_category_comparison = (
        "category average" in question_lower
        or "average purchase amount of their category" in question_lower
        or "average of their category" in question_lower
    )

    if asks_category_comparison:
        if (
            "purchase_amount" in sql_check
            and "avg(" in sql_check
            and "category" not in sql_check
        ):
            warnings.append(
                "The question requires a category-level comparison, "
                "but the SQL does not reference category."
            )

    if warnings:
        return False, " | ".join(warnings)

    return True, "SQL passed semantic checks."


# ============================================================
# SQL GENERATION
# ============================================================

def generate_sql(standalone_question):
    initialize_database()

    prompt = f"""
You are a MySQL SQL generator for an AI data analyst.

Your job is to determine whether the user's question
can be answered using the available database and,
if supported, generate ONE correct MySQL SELECT query.

DATABASE:
Database: test

TABLE:
customer_shopping

SCHEMA:
{_schema}

ALLOWED CATEGORICAL VALUES:
"""

    for column, values in _categorical_values.items():
        prompt += f"\n{column}: {', '.join(map(str, values))}"

    prompt += f"""

IMPORTANT RULES:

1. Understand the user's question carefully.
2. Use ONLY the information explicitly requested by the user.
3. DO NOT add filters, conditions, columns, GROUP BY clauses,
   or ORDER BY clauses that the user did not request.
4. If the user asks for an average, use AVG().
5. If the user asks for a total, use SUM().
6. If the user asks for a count, use COUNT().
7. If the user asks for the highest or lowest value for a
   single overall metric, ORDER BY and LIMIT may be appropriate.
8. For comparison questions such as "Which category has the
   highest total purchase amount?", "Which categories have the
   highest average rating?", or similar group-ranking questions,
   return ALL relevant groups ordered by the requested metric.
   Do NOT use LIMIT 1 unless the user explicitly asks for only
   the single winning row/value. This allows the application to
   show a complete comparison and visualization.
9. Never assume additional conditions.
9. Use only the table and columns provided.
10. Use only categorical values from the allowed values.
11. Generate exactly ONE SELECT query.
12. Never modify the database.
13. Return ONLY valid JSON.
14. The JSON must contain exactly these three fields:
    "sql", "supported", and "reason".
15. Do NOT use markdown.
16. Do NOT add explanations outside the JSON.
17. Do NOT add any fields other than:
    "sql", "supported", and "reason".
18. The SQL inside the "sql" field must be a valid MySQL
    SELECT query when "supported" is true.
19. If the user's question cannot be answered using the
    provided database schema and allowed values, do NOT
    generate SQL.
20. Never invent columns, tables, values, or information.

CTE SUPPORT:
21. CTEs using WITH are supported. Use a CTE when it makes a
    multi-step analytical query clearer or more reliable.
22. A CTE must ultimately produce one SELECT query and may use
    customer_shopping or previously defined CTEs only.

COMPLEX SQL PATTERNS:

23. When comparing an individual row against a group-level
    aggregate, use a derived table, CTE, JOIN, or correlated
    subquery.

24. When finding the row associated with MAX() or MIN(),
    do NOT select unrelated non-aggregated columns with GROUP BY.
    Calculate the aggregate separately and connect the row
    using a JOIN, subquery, CTE, or appropriate window function.

25. When using a window function for a group-level calculation,
    partition only by the grouping column.
    Example:
    AVG(purchase_amount) OVER (PARTITION BY category)

26. If a question requires comparing rows with an aggregate,
    do not mark the question unsupported merely because it
    requires a JOIN, subquery, CTE, or window function.

27. For highest, lowest, top, or bottom questions, distinguish
    between finding the aggregate value and finding the row
    associated with that value.

28. Never select a non-aggregated column that is not included
    in GROUP BY unless it is properly connected through a JOIN,
    window function, or appropriate subquery.
29. When the user asks for ALL rows tied for the highest or lowest
    value within each group, never use ROW_NUMBER() to choose one
    row. Use RANK(), DENSE_RANK(), or MAX()/MIN() with a JOIN so
    every tied row is returned.

SUPPORTED FORMAT:
{{
    "sql": "SELECT ...",
    "supported": true,
    "reason": ""
}}

UNSUPPORTED FORMAT:
{{
    "sql": null,
    "supported": false,
    "reason": "The question cannot be answered using the available database."
}}

USER QUESTION:
{standalone_question}
"""

    raw = call_ollama(prompt, temperature=0)
    raw = clean_json_response(raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(
            "Ollama returned invalid JSON. The SQL was not executed."
        )

    if not isinstance(data, dict):
        raise RuntimeError("AI response is not a JSON object.")

    if not all(key in data for key in ("sql", "supported", "reason")):
        raise RuntimeError(
            "AI response does not contain the required JSON fields."
        )

    if not isinstance(data["supported"], bool):
        raise RuntimeError("Invalid 'supported' value.")

    if not isinstance(data["reason"], str):
        raise RuntimeError("Invalid 'reason' value.")

    if data["supported"] is False:
        return {
            "supported": False,
            "reason": data["reason"],
            "sql": None
        }

    if not isinstance(data["sql"], str) or not data["sql"].strip():
        raise RuntimeError("The AI returned an empty SQL query.")

    return {
        "supported": True,
        "reason": data["reason"],
        "sql": data["sql"].strip()
    }


# ============================================================
# SEMANTIC SQL REPAIR
# ============================================================

def semantic_repair(sql, standalone_question, semantic_message):
    initialize_database()

    prompt = f"""
You are a MySQL SQL correctness assistant.

USER QUESTION:
{standalone_question}

GENERATED SQL:
{sql}

SEMANTIC VALIDATION ERROR:
{semantic_message}

DATABASE:
test

TABLE:
customer_shopping

SCHEMA:
{_schema}

YOUR TASK:
Rewrite the SQL so that it answers the user's question correctly,
not merely so that it executes.

IMPORTANT:
1. Return ONLY valid JSON.
2. Do not use Markdown or code fences.
3. JSON fields: sql, supported, reason.
4. Return exactly ONE SELECT statement; it may be preceded by a WITH CTE block.
5. Use only customer_shopping and its schema.
6. Preserve the user's exact intent.
7. CTEs are supported. Use WITH when it makes the multi-step
   logic clearer, as long as the final statement is a SELECT.
8. For MAX/MIN row questions, use a JOIN, subquery, CTE,
   or appropriate window function to retrieve the actual row
   associated with the aggregate.
9. For category-level averages, partition/group by category,
   not customer_id.
10. For row-vs-group comparisons, calculate the group aggregate
    correctly and connect it to the row.
11. Do not invent conditions or values.
12. If the user explicitly asks for all tied highest/lowest rows,
    do not use ROW_NUMBER(). Use RANK(), DENSE_RANK(), or
    MAX()/MIN() with a JOIN so ties are preserved.

RETURN FORMAT:
{{
    "sql": "SELECT ...",
    "supported": true,
    "reason": ""
}}
"""

    raw = clean_json_response(call_ollama(prompt, temperature=0))

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError("Semantic repair returned invalid JSON.")

    if (
        not isinstance(data, dict)
        or data.get("supported") is not True
        or not isinstance(data.get("sql"), str)
    ):
        raise RuntimeError("Semantic repair returned an invalid response.")

    repaired_sql = data["sql"].strip()

    safe, message = validate_sql(repaired_sql)

    if not safe:
        raise RuntimeError(
            f"Semantic repair produced unsafe SQL: {message}"
        )

    semantic_valid, semantic_message = validate_sql_semantics(
        repaired_sql,
        standalone_question
    )

    if not semantic_valid:
        raise RuntimeError(
            f"Semantic repair did not resolve the problem: "
            f"{semantic_message}"
        )

    return repaired_sql


# ============================================================
# SQL ERROR REPAIR
# ============================================================

def sql_error_repair(sql, standalone_question, mysql_error):
    initialize_database()

    prompt = f"""
You are a MySQL SQL debugging assistant for an AI data analyst.

USER QUESTION:
{standalone_question}

GENERATED SQL:
{sql}

MYSQL ERROR:
{mysql_error}

DATABASE:
test

TABLE:
customer_shopping

SCHEMA:
{_schema}

ALLOWED CATEGORICAL VALUES:
"""

    for column, values in _categorical_values.items():
        prompt += f"\n{column}: {', '.join(map(str, values))}"

    prompt += """

YOUR TASK:
Correct the SQL query so that it answers the original user
question correctly and is valid MySQL.

IMPORTANT RULES:
1. Return ONLY valid JSON.
2. Do NOT use Markdown or code fences.
3. JSON fields: sql, supported, reason.
4. Generate exactly ONE SELECT query.
5. Use only customer_shopping and columns from the schema.
6. Preserve the user's original intent.
7. Do not invent columns, tables, values, or conditions.
8. If finding a row associated with MAX() or MIN(), do not
   incorrectly select unrelated columns with GROUP BY. Use a
   JOIN, subquery, CTE, or appropriate window function.
9. If comparing individual rows with a group-level aggregate,
   use a JOIN, subquery, CTE, or correlated subquery.
10. Correct the specific MySQL error.
11. The corrected SQL must be safe to execute as a SELECT query.

RETURN FORMAT:
{
    "sql": "SELECT ...",
    "supported": true,
    "reason": ""
}
"""

    raw = clean_json_response(call_ollama(prompt, temperature=0))

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError("SQL repair returned invalid JSON.")

    if (
        not isinstance(data, dict)
        or data.get("supported") is not True
        or not isinstance(data.get("sql"), str)
    ):
        raise RuntimeError("SQL repair returned an invalid response.")

    repaired_sql = data["sql"].strip()

    safe, message = validate_sql(repaired_sql)

    if not safe:
        raise RuntimeError(
            f"Repaired query was blocked: {message}"
        )

    return repaired_sql


# ============================================================
# RESULT FORMATTING
# ============================================================

def format_result(cursor, result):
    column_names = [
        description[0]
        for description in cursor.description
    ]

    formatted = [
        dict(zip(column_names, row))
        for row in result
    ]

    def display_value(value):
        if isinstance(value, Decimal):
            return round(float(value), 2)
        if isinstance(value, float):
            return round(value, 2)
        return value

    return [
        {key: display_value(value) for key, value in row.items()}
        for row in formatted
    ]


def get_chart_data(formatted_result):
    """
    Return simple chart metadata when the result has exactly
    one text column and one numeric column.
    """

    if len(formatted_result) < 2:
        return None

    columns = list(formatted_result[0].keys())

    text_columns = []
    numeric_columns = []

    for column in columns:
        value = formatted_result[0][column]

        if isinstance(value, (int, float)):
            numeric_columns.append(column)
        else:
            text_columns.append(column)

    if len(text_columns) != 1 or len(numeric_columns) != 1:
        return None

    category_column = text_columns[0]
    value_column = numeric_columns[0]

    return {
        "category_column": category_column,
        "value_column": value_column,
        "categories": [
            row[category_column]
            for row in formatted_result
        ],
        "values": [
            row[value_column]
            for row in formatted_result
        ]
    }


# ============================================================
# NATURAL-LANGUAGE ANSWER
# ============================================================

def generate_answer(standalone_question, formatted_result):
    result_row_count = len(formatted_result)
    result_columns = list(formatted_result[0].keys())

    tie_summary = []

    if (
        "category" in result_columns
        and "purchase_amount" in result_columns
    ):
        category_groups = {}

        for row in formatted_result:
            category = row["category"]
            amount = row["purchase_amount"]

            category_groups.setdefault(
                category, []
            ).append(amount)

        for category, amounts in category_groups.items():
            maximum = max(amounts)
            count = amounts.count(maximum)

            if count > 1:
                tie_summary.append(
                    f"{category}: {count} rows tied at {maximum}"
                )

    tie_information = (
        "\n".join(tie_summary)
        if tie_summary
        else "No repeated maximum-value ties were detected."
    )

    prompt = f"""
You are a precise AI data analyst.

Answer the user's question using ONLY the database result.

USER QUESTION:
{standalone_question}

DATABASE RESULT:
{formatted_result}

RESULT ROW COUNT:
{result_row_count}

RESULT COLUMNS:
{result_columns}

TIE INFORMATION:
{tie_information}

STRICT RULES:

1. Use ONLY facts present in the database result.
2. NEVER invent, alter, merge, swap, or guess values.
3. NEVER associate a value from one row with a different row.
4. If multiple rows satisfy the question, do NOT pretend there is
   only one result.
5. If multiple customers are tied for the highest value in a
   category, explicitly say that there is a tie.
6. If the result contains more than 15 rows, DO NOT list individual
   rows. Give a concise summary and mention the exact number of
   matching rows.
7. For large results, do not choose a few rows as representatives
   unless the user explicitly asked for examples.
8. Preserve exact relationships between columns.
9. If the result contains one row, report that row accurately.
10. If the result contains 15 or fewer rows, present the rows
    clearly when appropriate.
11. Do not mention SQL.
12. Do not mention prompts.
13. Do not mention the model.
14. Do not mention internal processing.
15. For decimal/float values, display at most 2 decimal
    places unless the user explicitly asks for more precision.
16. Return ONLY the natural-language answer.
"""

    return call_ollama(prompt, temperature=0)


# ============================================================
# MAIN BACKEND FUNCTION FOR STREAMLIT
# ============================================================

def ask_data_analyst(question, conversation_history=None):
    """
    Run the complete AI Data Analyst pipeline.

    Returns a dictionary designed for the Streamlit UI.
    """

    if conversation_history is None:
        conversation_history = []

    question = question.strip()

    if not question:
        return {
            "success": False,
            "error": "Please enter a question."
        }

    conn = None
    cursor = None

    try:
        initialize_database()

        # 1. Resolve conversation context
        standalone_question = resolve_context(
            question,
            conversation_history
        )

        # 2. Generate SQL / determine support
        generated = generate_sql(standalone_question)

        if generated["supported"] is False:
            conversation_history.append({
                "question": question,
                "standalone_question": standalone_question,
                "sql": None,
                "result": [],
                "answer": generated["reason"]
            })

            return {
                "success": True,
                "supported": False,
                "question": question,
                "standalone_question": standalone_question,
                "sql": None,
                "result": [],
                "answer": generated["reason"],
                "chart": None
            }

        sql = generated["sql"]

        # 3. Safety validation
        safe, validation_message = validate_sql(sql)

        if not safe:
            return {
                "success": False,
                "error": f"Query blocked: {validation_message}"
            }

        # 4. Semantic validation + repair
        semantic_valid, semantic_message = validate_sql_semantics(
            sql,
            standalone_question
        )

        if not semantic_valid:
            sql = semantic_repair(
                sql,
                standalone_question,
                semantic_message
            )

        # 5. Execute SQL with automatic repair
        conn = get_connection()
        cursor = conn.cursor()

        max_retries = 2
        attempt = 0

        while attempt <= max_retries:
            try:
                cursor.execute(sql)
                result = cursor.fetchall()
                break

            except Error as exc:
                if attempt >= max_retries:
                    raise RuntimeError(
                        "SQL repair attempts exhausted."
                    ) from exc

                attempt += 1

                sql = sql_error_repair(
                    sql,
                    standalone_question,
                    str(exc)
                )

        # 6. Handle empty result
        if not result:
            answer = "No matching data was found."

            conversation_history.append({
                "question": question,
                "standalone_question": standalone_question,
                "sql": sql,
                "result": [],
                "answer": answer
            })

            return {
                "success": True,
                "supported": True,
                "question": question,
                "standalone_question": standalone_question,
                "sql": sql,
                "result": [],
                "answer": answer,
                "chart": None
            }

        if result == [(None,)]:
            answer = "No matching data was found."

            conversation_history.append({
                "question": question,
                "standalone_question": standalone_question,
                "sql": sql,
                "result": [],
                "answer": answer
            })

            return {
                "success": True,
                "supported": True,
                "question": question,
                "standalone_question": standalone_question,
                "sql": sql,
                "result": [],
                "answer": answer,
                "chart": None
            }

        # 7. Format result
        formatted_result = format_result(
            cursor,
            result
        )

        # 8. Generate answer
        answer = generate_answer(
            standalone_question,
            formatted_result
        )

        # 9. Prepare chart metadata
        chart = get_chart_data(formatted_result)

        # 10. Save conversation
        conversation_history.append({
            "question": question,
            "standalone_question": standalone_question,
            "sql": sql,
            "result": formatted_result,
            "answer": answer
        })

        return {
            "success": True,
            "supported": True,
            "question": question,
            "standalone_question": standalone_question,
            "sql": sql,
            "result": formatted_result,
            "answer": answer,
            "chart": chart
        }

    except requests.exceptions.ConnectionError:
        return {
            "success": False,
            "error": (
                "Could not connect to Ollama. "
                "Make sure Ollama is running."
            )
        }

    except requests.exceptions.Timeout:
        return {
            "success": False,
            "error": "The AI request took too long."
        }

    except mysql.connector.Error as exc:
        return {
            "success": False,
            "error": f"MySQL error: {exc}"
        }

    except Exception as exc:
        return {
            "success": False,
            "error": str(exc)
        }

    finally:
        if cursor is not None:
            cursor.close()

        if conn is not None and conn.is_connected():
            conn.close()
