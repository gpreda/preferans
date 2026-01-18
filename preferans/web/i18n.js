// Internationalization (i18n) module for Preferans
// Fetches translations from database via API

// Fallback translations (used when API is unavailable)
const fallbackTranslations = {
    en: {
        title: 'Preferans',
        newGame: 'New Game',
        player: 'Player',
        player1: 'Player 1',
        player2: 'Player 2',
        player3: 'Player 3',
        score: 'Score',
        tricks: 'Tricks',
        declarer: 'Declarer',
        serverConnectionFailed: 'Server connection failed',
        // Bid labels
        pass: 'Pass',
        game: 'Game',
        inHand: 'In Hand',
        betl: 'Betl',
        sans: 'Sans',
        hold: 'Hold',
        bidding: 'Bidding',
        biddingHistory: 'Bidding',
        dropCardHere: 'Drop card here',
        lastTrick: 'Last Trick',
        mustAnnounceContractFirst: 'You must announce a contract first',
        // Exchange
        discardTwoCards: 'Select 2 cards to discard',
        confirmDiscard: 'Confirm Discard',
        selectTwoCards: 'Select {0} more card(s)',
        talonPickedUp: 'Talon cards added to hand',
        cardsDiscarded: 'Cards discarded',
        // Phases
        'phases.auction': 'Auction',
        'phases.exchanging': 'Exchange',
        'phases.playing': 'Playing',
        'phases.scoring': 'Scoring',
        // Suits
        'suits.spades': 'Spades',
        'suits.hearts': 'Hearts',
        'suits.diamonds': 'Diamonds',
        'suits.clubs': 'Clubs'
    },
    sr: {
        title: 'Preferans',
        newGame: 'Nova igra',
        player: 'Igrač',
        player1: 'Igrač 1',
        player2: 'Igrač 2',
        player3: 'Igrač 3',
        score: 'Poeni',
        tricks: 'Štihovi',
        declarer: 'Igrač',
        serverConnectionFailed: 'Neuspešna konekcija sa serverom',
        // Bid labels
        pass: 'Pas',
        game: 'Igra',
        inHand: 'Iz ruke',
        betl: 'Betl',
        sans: 'Sans',
        hold: 'Držim',
        bidding: 'Licitacija',
        biddingHistory: 'Licitacija',
        dropCardHere: 'Spusti kartu ovde',
        lastTrick: 'Prethodni štih',
        mustAnnounceContractFirst: 'Morate prvo objaviti ugovor',
        // Exchange
        discardTwoCards: 'Izaberi 2 karte za odbacivanje',
        confirmDiscard: 'Potvrdi odbacivanje',
        selectTwoCards: 'Izaberi još {0} kart(u/e)',
        talonPickedUp: 'Talon karte dodate u ruku',
        cardsDiscarded: 'Karte odbačene',
        // Phases
        'phases.auction': 'Licitacija',
        'phases.exchanging': 'Razmena',
        'phases.playing': 'Igra',
        'phases.scoring': 'Bodovanje',
        // Suits
        'suits.spades': 'Pik',
        'suits.hearts': 'Herc',
        'suits.diamonds': 'Karo',
        'suits.clubs': 'Tref'
    }
};

// Current state
let translations = {};
let currentLang = localStorage.getItem('preferans-lang') || 'en';
let availableLanguages = [];
let isLoaded = false;

// Load translations from API
async function loadTranslations() {
    try {
        // Load languages
        const langResponse = await fetch('/api/i18n/languages');
        if (langResponse.ok) {
            availableLanguages = await langResponse.json();
        }

        // Load all translations
        const transResponse = await fetch('/api/i18n/translations');
        if (transResponse.ok) {
            translations = await transResponse.json();
            isLoaded = true;
        }
    } catch (error) {
        console.warn('Failed to load translations from API, using fallbacks:', error);
        translations = fallbackTranslations;
        availableLanguages = [
            { code: 'en', name: 'English', native_name: 'English', is_default: true },
            { code: 'sr', name: 'Serbian', native_name: 'Srpski', is_default: false }
        ];
    }

    // Validate current language
    if (!translations[currentLang]) {
        currentLang = 'en';
    }

    updatePageTranslations();
}

// Get translation for a key
function t(key, ...args) {
    const langTranslations = translations[currentLang] || translations['en'] || fallbackTranslations['en'];

    let value = langTranslations[key];

    // If not found in current language, try English
    if (value === undefined && currentLang !== 'en') {
        const enTranslations = translations['en'] || fallbackTranslations['en'];
        value = enTranslations[key];
    }

    // If still not found, return the key
    if (value === undefined) {
        return key;
    }

    // Replace placeholders {0}, {1}, etc.
    if (typeof value === 'string' && args.length > 0) {
        args.forEach((arg, i) => {
            value = value.replace(`{${i}}`, arg);
        });
    }

    return value;
}

// Set language
function setLanguage(lang) {
    if (translations[lang] || fallbackTranslations[lang]) {
        currentLang = lang;
        localStorage.setItem('preferans-lang', lang);
        updatePageTranslations();
        return true;
    }
    return false;
}

// Get current language
function getLanguage() {
    return currentLang;
}

// Get available languages
function getAvailableLanguages() {
    return availableLanguages;
}

// Check if translations are loaded
function isTranslationsLoaded() {
    return isLoaded;
}

// Update all elements with data-i18n attribute
function updatePageTranslations() {
    // Update elements with data-i18n attribute
    document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        el.textContent = t(key);
    });

    // Update elements with data-i18n-placeholder attribute
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        const key = el.getAttribute('data-i18n-placeholder');
        el.placeholder = t(key);
    });

    // Update elements with data-i18n-title attribute
    document.querySelectorAll('[data-i18n-title]').forEach(el => {
        const key = el.getAttribute('data-i18n-title');
        el.title = t(key);
    });

    // Update document title
    document.title = t('title');

    // Update html lang attribute
    document.documentElement.lang = currentLang;
}

// Reload translations from server
async function reloadTranslations() {
    await loadTranslations();
}

// Initialize on load
loadTranslations();

// Export for use in app.js
window.i18n = {
    t,
    setLanguage,
    getLanguage,
    getAvailableLanguages,
    updatePageTranslations,
    reloadTranslations,
    isTranslationsLoaded
};
