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


# i18n - Languages

def get_all_languages():
    """Get all available languages."""
    with get_db_cursor() as cur:
        cur.execute('''
            SELECT id, code, name, native_name, is_default, created_at
            FROM languages ORDER BY is_default DESC, name
        ''')
        return cur.fetchall()


def get_language(code: str = None, language_id: int = None):
    """Get a language by code or ID."""
    with get_db_cursor() as cur:
        if language_id:
            cur.execute('SELECT * FROM languages WHERE id = %s', (language_id,))
        elif code:
            cur.execute('SELECT * FROM languages WHERE code = %s', (code,))
        else:
            cur.execute('SELECT * FROM languages WHERE is_default = TRUE')
        return cur.fetchone()


def add_language(code: str, name: str, native_name: str = None, is_default: bool = False):
    """Add a new language."""
    with get_db_cursor(commit=True) as cur:
        cur.execute('''
            INSERT INTO languages (code, name, native_name, is_default)
            VALUES (%s, %s, %s, %s)
            RETURNING id, code, name, native_name, is_default
        ''', (code, name, native_name or name, is_default))
        return cur.fetchone()


# i18n - Translations

def get_translations(language_code: str):
    """Get all translations for a language as a dict."""
    lang = get_language(code=language_code)
    if not lang:
        return {}

    with get_db_cursor() as cur:
        cur.execute('''
            SELECT key, value FROM translations
            WHERE language_id = %s
        ''', (lang['id'],))
        rows = cur.fetchall()
        return {row['key']: row['value'] for row in rows}


def get_all_translations():
    """Get all translations for all languages."""
    with get_db_cursor() as cur:
        cur.execute('''
            SELECT l.code as language_code, t.key, t.value
            FROM translations t
            JOIN languages l ON t.language_id = l.id
            ORDER BY l.code, t.key
        ''')
        rows = cur.fetchall()

        result = {}
        for row in rows:
            lang = row['language_code']
            if lang not in result:
                result[lang] = {}
            result[lang][row['key']] = row['value']
        return result


def get_all_translation_keys():
    """Get all unique translation keys."""
    with get_db_cursor() as cur:
        cur.execute('SELECT DISTINCT key FROM translations ORDER BY key')
        return [row['key'] for row in cur.fetchall()]


def update_translation(language_code: str, key: str, value: str):
    """Update or insert a translation."""
    lang = get_language(code=language_code)
    if not lang:
        return None

    with get_db_cursor(commit=True) as cur:
        cur.execute('''
            INSERT INTO translations (language_id, key, value)
            VALUES (%s, %s, %s)
            ON CONFLICT (language_id, key) DO UPDATE SET
                value = EXCLUDED.value,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id, key, value
        ''', (lang['id'], key, value))
        return cur.fetchone()


def delete_translation(language_code: str, key: str):
    """Delete a translation."""
    lang = get_language(code=language_code)
    if not lang:
        return False

    with get_db_cursor(commit=True) as cur:
        cur.execute('''
            DELETE FROM translations
            WHERE language_id = %s AND key = %s
        ''', (lang['id'], key))
        return cur.rowcount > 0
