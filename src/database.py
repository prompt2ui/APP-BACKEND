# src/database.py
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("SUPABASE_DIRECT_URL")


def get_connection():
    """Create and return a new database connection."""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    """Test database connection on startup."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT NOW();")
        result = cursor.fetchone()
        print(f"✅ Database connected! Server time: {result['now']}")
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"❌ Failed to connect to database: {e}")
        raise


def execute_query(query: str, params: tuple = None, fetch: str = "all"):
    """
    Execute a SQL query and return results.
    
    Args:
        query: SQL query string
        params: Query parameters (tuple)
        fetch: "all" | "one" | "none"
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query, params)

        if fetch == "all":
            result = cursor.fetchall()
        elif fetch == "one":
            result = cursor.fetchone()
        else:
            result = None

        conn.commit()
        cursor.close()
        return result
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()