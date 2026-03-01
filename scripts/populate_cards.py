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

    # Define all styles
    styles = [
        ('classic', 'Classic card design with Arial sans-serif font', True),
        ('elegant', 'Elegant design with Georgia serif font', False),
        ('typewriter', 'Typewriter style with Courier monospace font', False),
        ('modern', 'Modern design with Verdana sans-serif font', False),
        ('bold', 'Bold design with Impact font', False),
        ('playful', 'Playful design with Comic Sans font', False),
        ('vintage', 'Vintage design with Palatino serif font', False),
    ]

    style_names = [s[0] for s in styles]

    # Remove styles not in our list
    print("Removing old styles...")
    cur.execute(
        "DELETE FROM card_images WHERE style_id IN (SELECT id FROM deck_styles WHERE name != ALL(%s))",
        (style_names,)
    )
    cur.execute("DELETE FROM deck_styles WHERE name != ALL(%s)", (style_names,))

    # Create and populate each style
    for name, description, is_default in styles:
        print(f"Creating '{name}' deck style...")
        style_id = create_style(cur, name=name, description=description, is_default=is_default)
        print(f"Populating cards for {name} style (ID {style_id})...")
        populate_style_cards(cur, style_id, style=name)

    conn.commit()
    cur.close()
    conn.close()
    print("Cards populated successfully!")


if __name__ == '__main__':
    populate_cards()
