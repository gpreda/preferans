"""Populate the database with card graphics."""
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from server.config import DATABASE_CONFIG
from generate_cards import generate_all_cards, generate_card_back_svg


def create_style(cur, name: str, description: str, is_default: bool = False) -> int:
    """Create a new deck style and return its ID."""
    back_svg = generate_card_back_svg()

    cur.execute('''
        INSERT INTO deck_styles (name, description, back_image_type, back_image_svg, is_default)
        VALUES (%s, %s, 'svg', %s, %s)
        ON CONFLICT (name) DO UPDATE SET
            description = EXCLUDED.description,
            back_image_svg = EXCLUDED.back_image_svg,
            is_default = EXCLUDED.is_default
        RETURNING id
    ''', (name, description, back_svg, is_default))

    return cur.fetchone()[0]


def populate_style_cards(cur, style_id: int):
    """Populate all card images for a style."""
    cards = generate_all_cards()

    for card_id, card_data in cards.items():
        if card_id == 'back':
            continue  # Back is stored in deck_styles table

        cur.execute('''
            INSERT INTO card_images (style_id, card_id, rank, suit, image_type, image_svg)
            VALUES (%s, %s, %s, %s, 'svg', %s)
            ON CONFLICT (style_id, card_id) DO UPDATE SET
                image_svg = EXCLUDED.image_svg,
                image_type = EXCLUDED.image_type
        ''', (style_id, card_id, card_data['rank'], card_data['suit'], card_data['svg']))


def populate_cards():
    """Create default style and insert all card SVGs."""
    conn = psycopg2.connect(**DATABASE_CONFIG)
    cur = conn.cursor()

    print("Creating 'classic' deck style...")
    style_id = create_style(
        cur,
        name='classic',
        description='Classic simple SVG card design',
        is_default=True
    )

    print(f"Populating cards for style ID {style_id}...")
    populate_style_cards(cur, style_id)

    conn.commit()
    cur.close()
    conn.close()
    print("Cards populated successfully!")


if __name__ == '__main__':
    populate_cards()
