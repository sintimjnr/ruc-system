import psycopg2
import os

conn = psycopg2.connect(
    host=os.environ.get("DB_HOST", "localhost"),
    database=os.environ.get("DB_NAME", "ruc_system"),
    user=os.environ.get("DB_USER", "postgres"),
    password=os.environ.get("DB_PASSWORD", "3598"),
    port=os.environ.get("DB_PORT", "5432"),
)

print("Database connected successfully!")

conn.close()
