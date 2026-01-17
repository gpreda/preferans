-- Migration: Add deck styles support with flexible image storage

-- Drop old cards table
DROP TABLE IF EXISTS cards CASCADE;

-- Deck styles table
CREATE TABLE IF NOT EXISTS deck_styles (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,
    description TEXT,
    -- Card back image (one per style)
    back_image_type VARCHAR(10) NOT NULL DEFAULT 'svg',  -- 'svg', 'png', 'jpg', 'webp'
    back_image_svg TEXT,
    back_image_binary BYTEA,
    is_default BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Card images table (linked to styles)
CREATE TABLE IF NOT EXISTS card_images (
    id SERIAL PRIMARY KEY,
    style_id INTEGER REFERENCES deck_styles(id) ON DELETE CASCADE,
    card_id VARCHAR(20) NOT NULL,  -- e.g., 'A_spades', '7_hearts'
    rank VARCHAR(2) NOT NULL,
    suit VARCHAR(10) NOT NULL,
    image_type VARCHAR(10) NOT NULL DEFAULT 'svg',  -- 'svg', 'png', 'jpg', 'webp'
    image_svg TEXT,
    image_binary BYTEA,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(style_id, card_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_deck_styles_name ON deck_styles(name);
CREATE INDEX IF NOT EXISTS idx_deck_styles_default ON deck_styles(is_default);
CREATE INDEX IF NOT EXISTS idx_card_images_style ON card_images(style_id);
CREATE INDEX IF NOT EXISTS idx_card_images_card_id ON card_images(card_id);

-- Ensure only one default style
CREATE OR REPLACE FUNCTION ensure_single_default_style()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.is_default = TRUE THEN
        UPDATE deck_styles SET is_default = FALSE WHERE id != NEW.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS single_default_style ON deck_styles;
CREATE TRIGGER single_default_style
    AFTER INSERT OR UPDATE ON deck_styles
    FOR EACH ROW
    WHEN (NEW.is_default = TRUE)
    EXECUTE FUNCTION ensure_single_default_style();
