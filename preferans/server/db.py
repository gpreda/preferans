"""Database connection and queries."""
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
from config import DATABASE_CONFIG


@contextmanager
def get_db_connection():
    """Get a database connection context manager."""
    conn = psycopg2.connect(**DATABASE_CONFIG)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def get_db_cursor(commit=False):
    """Get a database cursor context manager."""
    with get_db_connection() as conn:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        try:
            yield cursor
            if commit:
                conn.commit()
        finally:
            cursor.close()


# Deck Styles

def get_all_styles():
    """Get all deck styles."""
    with get_db_cursor() as cur:
        cur.execute('''
            SELECT id, name, description, back_image_type, is_default, created_at
            FROM deck_styles ORDER BY id
        ''')
        return cur.fetchall()


def get_style(style_id: int = None, name: str = None):
    """Get a style by ID or name. If neither provided, returns default style."""
    with get_db_cursor() as cur:
        if style_id:
            cur.execute('SELECT * FROM deck_styles WHERE id = %s', (style_id,))
        elif name:
            cur.execute('SELECT * FROM deck_styles WHERE name = %s', (name,))
        else:
            cur.execute('SELECT * FROM deck_styles WHERE is_default = TRUE')
        return cur.fetchone()


def get_style_back_image(style_id: int = None, name: str = None):
    """Get the back image for a style."""
    style = get_style(style_id, name)
    if style:
        return {
            'type': style['back_image_type'],
            'svg': style['back_image_svg'],
            'binary': style['back_image_binary']
        }
    return None


# Card Images

def get_all_cards(style_id: int = None, style_name: str = None):
    """Get all cards for a style. Uses default style if not specified."""
    style = get_style(style_id, style_name)
    if not style:
        return []

    with get_db_cursor() as cur:
        cur.execute('''
            SELECT card_id, rank, suit, image_type
            FROM card_images
            WHERE style_id = %s
            ORDER BY id
        ''', (style['id'],))
        return cur.fetchall()


def get_card(card_id: str, style_id: int = None, style_name: str = None):
    """Get a single card by ID for a style."""
    style = get_style(style_id, style_name)
    if not style:
        return None

    with get_db_cursor() as cur:
        cur.execute('''
            SELECT card_id, rank, suit, image_type, image_svg, image_binary
            FROM card_images
            WHERE style_id = %s AND card_id = %s
        ''', (style['id'], card_id))
        return cur.fetchone()


def get_card_image(card_id: str, style_id: int = None, style_name: str = None):
    """Get card image data."""
    card = get_card(card_id, style_id, style_name)
    if card:
        return {
            'type': card['image_type'],
            'svg': card['image_svg'],
            'binary': card['image_binary']
        }
    return None
