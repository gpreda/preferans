"""Populate i18n tables with initial translations."""
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from server.db import get_db_connection

# Language definitions
LANGUAGES = [
    {'code': 'en', 'name': 'English', 'native_name': 'English', 'is_default': True},
    {'code': 'sr', 'name': 'Serbian', 'native_name': 'Srpski', 'is_default': False},
]

# Translations - flattened key structure
TRANSLATIONS = {
    'en': {
        # Header
        'title': 'Preferans',
        'newGame': 'New Game',

        # Players
        'player': 'Player',
        'player1': 'Player 1',
        'player2': 'Player 2',
        'player3': 'Player 3',
        'score': 'Score',
        'tricks': 'Tricks',
        'declarer': 'Declarer',

        # Game elements
        'talon': 'Talon',
        'talonCard': 'Talon card',

        # Phases
        'phases.waiting': 'WAITING',
        'phases.dealing': 'DEALING',
        'phases.auction': 'AUCTION',
        'phases.exchanging': 'EXCHANGING',
        'phases.playing': 'PLAYING',
        'phases.scoring': 'SCORING',
        'phases.finished': 'FINISHED',

        # Bidding
        'bidding': 'Bidding',
        'pass': 'Pass',

        # Exchange
        'pickUpTalon': 'Pick Up Talon',
        'selectCardsToDiscard': 'Select 2 cards to discard, then:',
        'discardSelected': 'Discard Selected',

        # Contract
        'announceContract': 'Announce Contract',
        'announce': 'Announce',
        'suit': 'Suit',
        'betl': 'Betl',
        'sans': 'Sans',
        'contract': 'Contract',
        'needTricks': 'Need {0} tricks',

        # Suits
        'suits.spades': 'Spades',
        'suits.hearts': 'Hearts',
        'suits.diamonds': 'Diamonds',
        'suits.clubs': 'Clubs',

        # Ranks
        'ranks.7': '7',
        'ranks.8': '8',
        'ranks.9': '9',
        'ranks.10': '10',
        'ranks.J': 'Jack',
        'ranks.Q': 'Queen',
        'ranks.K': 'King',
        'ranks.A': 'Ace',

        # Playing
        'clickCardToPlay': 'Click a card to play it',

        # Scoring
        'nextRound': 'Next Round',
        'roundOver': 'Round over! {0} took {1} tricks.',

        # Messages
        'serverStatus': 'Server: {0}',
        'serverConnectionFailed': 'Server connection failed',
        'startingNewGame': 'Starting new game...',
        'gameStarted': 'Game started! Bidding phase begins.',
        'playerPassed': '{0} passed',
        'playerBid': '{0} bid {1}',
        'talonPickedUp': 'Talon picked up. Select 2 cards to discard.',
        'cardsDiscarded': 'Cards discarded. Announce your contract.',
        'contractAnnounced': 'Contract announced: {0}',
        'contractAnnouncedWithSuit': 'Contract announced: {0} ({1})',
        'trickWonBy': 'Trick won by {0}!',
        'roundComplete': 'Round complete!',
        'newRoundStarted': 'New round started!',

        # Errors
        'failedToStartGame': 'Failed to start game: {0}',
        'failedToPlaceBid': 'Failed to place bid: {0}',
        'failedToPickUpTalon': 'Failed to pick up talon: {0}',
        'failedToDiscard': 'Failed to discard: {0}',
        'failedToAnnounceContract': 'Failed to announce contract: {0}',
        'failedToPlayCard': 'Failed to play card: {0}',
        'failedToStartNextRound': 'Failed to start next round: {0}',

        # Card name format
        'cardName': '{0} of {1}',

        # i18n editor
        'i18nEditor': 'Translation Editor',
        'language': 'Language',
        'key': 'Key',
        'value': 'Value',
        'save': 'Save',
        'saving': 'Saving...',
        'saved': 'Saved!',
        'addLanguage': 'Add Language',
        'languageCode': 'Language Code',
        'languageName': 'Language Name',
        'nativeName': 'Native Name',
        'cancel': 'Cancel',
        'add': 'Add',
        'filter': 'Filter keys...',
        'allKeys': 'All Keys',
        'missingOnly': 'Missing Only',
    },

    'sr': {
        # Header
        'title': 'Preferans',
        'newGame': 'Nova igra',

        # Players
        'player': 'Igrac',
        'player1': 'Igrac 1',
        'player2': 'Igrac 2',
        'player3': 'Igrac 3',
        'score': 'Poeni',
        'tricks': 'Stihovi',
        'declarer': 'Igra',

        # Game elements
        'talon': 'Talon',
        'talonCard': 'Karta iz talona',

        # Phases
        'phases.waiting': 'CEKANJE',
        'phases.dealing': 'DELJENJE',
        'phases.auction': 'LICITACIJA',
        'phases.exchanging': 'ZAMENA',
        'phases.playing': 'IGRA',
        'phases.scoring': 'BODOVANJE',
        'phases.finished': 'KRAJ',

        # Bidding
        'bidding': 'Licitacija',
        'pass': 'Dalje',

        # Exchange
        'pickUpTalon': 'Uzmi talon',
        'selectCardsToDiscard': 'Izaberi 2 karte za odbacivanje:',
        'discardSelected': 'Odbaci izabrane',

        # Contract
        'announceContract': 'Objavi igru',
        'announce': 'Objavi',
        'suit': 'Adut',
        'betl': 'Betl',
        'sans': 'Sans',
        'contract': 'Igra',
        'needTricks': 'Potrebno {0} stihova',

        # Suits
        'suits.spades': 'Pik',
        'suits.hearts': 'Herc',
        'suits.diamonds': 'Karo',
        'suits.clubs': 'Tref',

        # Ranks
        'ranks.7': '7',
        'ranks.8': '8',
        'ranks.9': '9',
        'ranks.10': '10',
        'ranks.J': 'Zandar',
        'ranks.Q': 'Dama',
        'ranks.K': 'Kralj',
        'ranks.A': 'Kec',

        # Playing
        'clickCardToPlay': 'Klikni na kartu da je odigras',

        # Scoring
        'nextRound': 'Sledeca runda',
        'roundOver': 'Runda zavrsena! {0} je uzeo {1} stihova.',

        # Messages
        'serverStatus': 'Server: {0}',
        'serverConnectionFailed': 'Neuspesna konekcija sa serverom',
        'startingNewGame': 'Pokretanje nove igre...',
        'gameStarted': 'Igra je pocela! Licitacija pocinje.',
        'playerPassed': '{0} je rekao dalje',
        'playerBid': '{0} je licitirao {1}',
        'talonPickedUp': 'Talon uzet. Izaberi 2 karte za odbacivanje.',
        'cardsDiscarded': 'Karte odbacene. Objavi svoju igru.',
        'contractAnnounced': 'Igra objavljena: {0}',
        'contractAnnouncedWithSuit': 'Igra objavljena: {0} ({1})',
        'trickWonBy': 'Stih osvojio {0}!',
        'roundComplete': 'Runda zavrsena!',
        'newRoundStarted': 'Nova runda je pocela!',

        # Errors
        'failedToStartGame': 'Neuspelo pokretanje igre: {0}',
        'failedToPlaceBid': 'Neuspela licitacija: {0}',
        'failedToPickUpTalon': 'Neuspelo uzimanje talona: {0}',
        'failedToDiscard': 'Neuspelo odbacivanje: {0}',
        'failedToAnnounceContract': 'Neuspela objava igre: {0}',
        'failedToPlayCard': 'Neuspelo igranje karte: {0}',
        'failedToStartNextRound': 'Neuspelo pokretanje sledece runde: {0}',

        # Card name format
        'cardName': '{0} {1}',

        # i18n editor
        'i18nEditor': 'Uredjivac prevoda',
        'language': 'Jezik',
        'key': 'Kljuc',
        'value': 'Vrednost',
        'save': 'Sacuvaj',
        'saving': 'Cuvanje...',
        'saved': 'Sacuvano!',
        'addLanguage': 'Dodaj jezik',
        'languageCode': 'Kod jezika',
        'languageName': 'Naziv jezika',
        'nativeName': 'Maticni naziv',
        'cancel': 'Otkazi',
        'add': 'Dodaj',
        'filter': 'Filtriraj kljuceve...',
        'allKeys': 'Svi kljucevi',
        'missingOnly': 'Samo nedostajuci',
    }
}


def populate_languages(cursor):
    """Insert languages into database."""
    for lang in LANGUAGES:
        cursor.execute('''
            INSERT INTO languages (code, name, native_name, is_default)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (code) DO UPDATE SET
                name = EXCLUDED.name,
                native_name = EXCLUDED.native_name,
                is_default = EXCLUDED.is_default
            RETURNING id
        ''', (lang['code'], lang['name'], lang['native_name'], lang['is_default']))
        lang['id'] = cursor.fetchone()[0]
        print(f"Language '{lang['code']}' inserted/updated with ID {lang['id']}")
    return {lang['code']: lang['id'] for lang in LANGUAGES}


def populate_translations(cursor, language_ids):
    """Insert translations into database."""
    count = 0
    for lang_code, translations in TRANSLATIONS.items():
        lang_id = language_ids.get(lang_code)
        if not lang_id:
            print(f"Warning: Language '{lang_code}' not found in database")
            continue

        for key, value in translations.items():
            cursor.execute('''
                INSERT INTO translations (language_id, key, value)
                VALUES (%s, %s, %s)
                ON CONFLICT (language_id, key) DO UPDATE SET
                    value = EXCLUDED.value,
                    updated_at = CURRENT_TIMESTAMP
            ''', (lang_id, key, value))
            count += 1

    print(f"Inserted/updated {count} translations")


def main():
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        print("Populating languages...")
        language_ids = populate_languages(cursor)

        print("Populating translations...")
        populate_translations(cursor, language_ids)

        conn.commit()
        print("i18n data populated successfully!")

    except Exception as e:
        conn.rollback()
        print(f"Error: {e}")
        raise
    finally:
        cursor.close()
        conn.close()


if __name__ == '__main__':
    main()
