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

    # If this style is default, first unset any existing default
    if is_default:
        cur.execute('UPDATE deck_styles SET is_default = FALSE WHERE is_default = TRUE')

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


def populate_style_cards(cur, style_id: int, style: str = 'classic'):
    """Populate all card images for a style."""
    cards = generate_all_cards(style=style)

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
    """Create deck styles and insert all card SVGs."""
    conn = psycopg2.connect(**DATABASE_CONFIG)
    cur = conn.cursor()

    # Remove old styles (keep only classic and elegant)
    print("Removing old styles...")
    cur.execute("DELETE FROM card_images WHERE style_id IN (SELECT id FROM deck_styles WHERE name NOT IN ('classic', 'elegant'))")
    cur.execute("DELETE FROM deck_styles WHERE name NOT IN ('classic', 'elegant')")

    # Classic style (default)
    print("Creating 'classic' deck style...")
    classic_id = create_style(
        cur,
        name='classic',
        description='Classic card design with centered ranks and corner suits',
        is_default=True
    )
    print(f"Populating cards for classic style (ID {classic_id})...")
    populate_style_cards(cur, classic_id, style='classic')

    # Elegant style
    print("Creating 'elegant' deck style...")
    elegant_id = create_style(
        cur,
        name='elegant',
        description='Elegant card design with Georgia serif font',
        is_default=False
    )
    print(f"Populating cards for elegant style (ID {elegant_id})...")
    populate_style_cards(cur, elegant_id, style='elegant')

    conn.commit()
    cur.close()
    conn.close()
    print("Cards populated successfully!")


if __name__ == '__main__':
    populate_cards()
