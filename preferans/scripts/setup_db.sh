#!/bin/bash

# Setup PostgreSQL database for Preferans

echo "Creating database 'preferans'..."
createdb preferans 2>/dev/null || echo "Database may already exist"

echo "Running schema initialization..."
psql -d preferans -f "$(dirname "$0")/init_db.sql"

echo "Installing Python dependencies..."
pip install psycopg2-binary

echo "Populating cards..."
cd "$(dirname "$0")" && python populate_cards.py

echo "Database setup complete!"
