-- Initialize Preferans database schema

-- Cards table
CREATE TABLE IF NOT EXISTS cards (
    id SERIAL PRIMARY KEY,
    card_id VARCHAR(20) UNIQUE NOT NULL,
    rank VARCHAR(2),
    suit VARCHAR(10),
    svg TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Players table
CREATE TABLE IF NOT EXISTS players (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    display_name VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Games table
CREATE TABLE IF NOT EXISTS games (
    id SERIAL PRIMARY KEY,
    status VARCHAR(20) DEFAULT 'waiting',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    ended_at TIMESTAMP
);

-- Game players (3 players per game)
CREATE TABLE IF NOT EXISTS game_players (
    id SERIAL PRIMARY KEY,
    game_id INTEGER REFERENCES games(id) ON DELETE CASCADE,
    player_id INTEGER REFERENCES players(id) ON DELETE CASCADE,
    position INTEGER CHECK (position >= 1 AND position <= 3),
    score INTEGER DEFAULT 0,
    UNIQUE(game_id, position)
);

-- Index for faster lookups
CREATE INDEX IF NOT EXISTS idx_cards_card_id ON cards(card_id);
CREATE INDEX IF NOT EXISTS idx_games_status ON games(status);
