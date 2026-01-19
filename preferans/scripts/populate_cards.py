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

    # Classic style (not default)
    print("Creating 'classic' deck style...")
    classic_id = create_style(
        cur,
        name='classic',
        description='Classic simple SVG card design',
        is_default=False
    )
    print(f"Populating cards for classic style (ID {classic_id})...")
    populate_style_cards(cur, classic_id, style='classic')

    # Compact style (not default)
    print("Creating 'compact' deck style...")
    compact_id = create_style(
        cur,
        name='compact',
        description='Compact design with L-shaped corner indicators, optimized for overlapping display',
        is_default=False
    )
    print(f"Populating cards for compact style (ID {compact_id})...")
    populate_style_cards(cur, compact_id, style='compact')

    # Large style (not default)
    print("Creating 'large' deck style...")
    large_id = create_style(
        cur,
        name='large',
        description='Large corner labels with L-shaped indicators, rank and suit same size',
        is_default=False
    )
    print(f"Populating cards for large style (ID {large_id})...")
    populate_style_cards(cur, large_id, style='large')

    # Centered style (default) - large corner ranks, suits centered on four edges
    print("Creating 'centered' deck style...")
    centered_id = create_style(
        cur,
        name='centered',
        description='Large corner ranks with suit symbols centered on all four edges',
        is_default=True
    )
    print(f"Populating cards for centered style (ID {centered_id})...")
    populate_style_cards(cur, centered_id, style='centered')

    conn.commit()
    cur.close()
    conn.close()
    print("Cards populated successfully!")


if __name__ == '__main__':
    populate_cards()
