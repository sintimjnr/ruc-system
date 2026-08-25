from app import ConfigurationError, connect_db


try:
    conn = connect_db()
except ConfigurationError as exc:
    raise SystemExit(f"Configuration error: {exc}") from None

print("Database connected successfully!")
conn.close()
