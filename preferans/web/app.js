document.addEventListener('DOMContentLoaded', async () => {
    const statusEl = document.getElementById('status');
    const shuffleBtn = document.getElementById('shuffle-btn');

    // Check server health
    try {
        const response = await fetch('/api/health');
        const data = await response.json();
        statusEl.textContent = `Server: ${data.status}`;
    } catch (error) {
        statusEl.textContent = 'Server connection failed';
    }

    // Shuffle button click handler
    shuffleBtn.addEventListener('click', shuffleAndDeal);

    // Initial deal
    shuffleAndDeal();
});

async function shuffleAndDeal() {
    const statusEl = document.getElementById('status');

    try {
        statusEl.textContent = 'Shuffling...';

        const response = await fetch('/api/game/shuffle', { method: 'POST' });
        const deal = await response.json();

        // Clear all player cards
        clearAllCards();

        // Deal cards to each player
        renderPlayerCards('player1', deal.player1);
        renderPlayerCards('player2', deal.player2);
        renderPlayerCards('player3', deal.player3);
        renderTalon(deal.talon);

        statusEl.textContent = 'Cards dealt!';
    } catch (error) {
        statusEl.textContent = 'Failed to shuffle';
        console.error(error);
    }
}

function clearAllCards() {
    document.querySelectorAll('.player-cards, .talon-cards').forEach(el => {
        el.innerHTML = '';
    });
}

function renderPlayerCards(playerId, cardIds) {
    const playerEl = document.getElementById(playerId);
    const cardsContainer = playerEl.querySelector('.player-cards');

    cardIds.forEach(cardId => {
        const img = createCardImage(cardId);
        cardsContainer.appendChild(img);
    });
}

function renderTalon(cardIds) {
    const talonEl = document.getElementById('talon');
    const cardsContainer = talonEl.querySelector('.talon-cards');

    cardIds.forEach(cardId => {
        const img = createCardImage(cardId);
        cardsContainer.appendChild(img);
    });
}

function createCardImage(cardId) {
    const img = document.createElement('img');
    img.src = `/api/cards/${cardId}/image`;
    img.alt = cardId;
    img.className = 'card';
    img.title = formatCardName(cardId);
    return img;
}

function formatCardName(cardId) {
    const [rank, suit] = cardId.split('_');
    return `${rank} of ${suit}`;
}
