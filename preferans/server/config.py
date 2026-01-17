"""Database and application configuration."""
import os

DATABASE_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': os.getenv('DB_PORT', '5432'),
    'database': os.getenv('DB_NAME', 'preferans'),
    'user': os.getenv('DB_USER', os.getenv('USER', 'postgres')),
    'password': os.getenv('DB_PASSWORD', ''),
}


def get_database_url():
    """Get PostgreSQL connection URL."""
    c = DATABASE_CONFIG
    return f"postgresql://{c['user']}:{c['password']}@{c['host']}:{c['port']}/{c['database']}"
