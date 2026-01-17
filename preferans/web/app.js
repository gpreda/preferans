// Preferans Game Client

let gameState = null;
let selectedCards = [];

// Shorthand for translation function
const t = (key, ...args) => window.i18n.t(key, ...args);

// DOM Elements
const elements = {
    newGameBtn: null,
    phaseIndicator: null,
    status: null,
    messageArea: null,
    languageSelector: null,
    // Players
    player1: null,
    player2: null,
    player3: null,
    // Center
    talon: null,
    currentTrick: null,
    contractInfo: null,
    // Action panels
    biddingControls: null,
    exchangeControls: null,
    contractControls: null,
    playControls: null,
    scoringControls: null,
};

document.addEventListener('DOMContentLoaded', () => {
    // Initialize DOM references
    elements.newGameBtn = document.getElementById('new-game-btn');
    elements.phaseIndicator = document.getElementById('phase-indicator');
    elements.status = document.getElementById('status');
    elements.messageArea = document.getElementById('message-area');
    elements.languageSelector = document.getElementById('language-selector');

    elements.player1 = document.getElementById('player1');
    elements.player2 = document.getElementById('player2');
    elements.player3 = document.getElementById('player3');

    elements.talon = document.getElementById('talon');
    elements.currentTrick = document.getElementById('current-trick');
    elements.contractInfo = document.getElementById('contract-info');

    elements.biddingControls = document.getElementById('bidding-controls');
    elements.exchangeControls = document.getElementById('exchange-controls');
    elements.contractControls = document.getElementById('contract-controls');
    elements.playControls = document.getElementById('play-controls');
    elements.scoringControls = document.getElementById('scoring-controls');

    // Event listeners
    elements.newGameBtn.addEventListener('click', startNewGame);

    // Language selector
    elements.languageSelector.value = window.i18n.getLanguage();
    elements.languageSelector.addEventListener('change', (e) => {
        window.i18n.setLanguage(e.target.value);
        if (gameState) {
            renderGame();
        }
    });

    // Bidding buttons
    document.querySelectorAll('.bid-btn').forEach(btn => {
        btn.addEventListener('click', () => placeBid(parseInt(btn.dataset.value)));
    });

    // Exchange buttons
    document.getElementById('pickup-talon-btn').addEventListener('click', pickUpTalon);
    document.getElementById('discard-btn').addEventListener('click', discardSelected);

    // Contract buttons
    document.getElementById('contract-type').addEventListener('change', updateTrumpVisibility);
    document.getElementById('announce-btn').addEventListener('click', announceContract);

    // Next round button
    document.getElementById('next-round-btn').addEventListener('click', nextRound);

    // Initialize translations
    window.i18n.updatePageTranslations();

    // Check server status
    checkServer();
});

async function checkServer() {
    try {
        const response = await fetch('/api/health');
        const data = await response.json();
        showMessage(t('serverStatus', data.status), 'success');
    } catch (error) {
        showMessage(t('serverConnectionFailed'), 'error');
    }
}

async function startNewGame() {
    try {
        showMessage(t('startingNewGame'));
        const response = await fetch('/api/game/new', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                players: [t('player1'), t('player2'), t('player3')]
            })
        });
        const data = await response.json();

        if (data.success) {
            gameState = data.state;
            selectedCards = [];
            renderGame();
            showMessage(t('gameStarted'), 'success');
        } else {
            showMessage(data.error, 'error');
        }
    } catch (error) {
        showMessage(t('failedToStartGame', error.message), 'error');
    }
}

async function placeBid(value) {
    if (!gameState) return;

    const currentBidderId = gameState.current_round?.auction?.current_bidder_id;
    if (!currentBidderId) return;

    try {
        const response = await fetch('/api/game/bid', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                player_id: currentBidderId,
                value: value,
                suit: value > 0 ? 'spades' : null  // Default to spades for simplicity
            })
        });
        const data = await response.json();

        if (data.success) {
            gameState = data.state;
            renderGame();
            const playerName = getPlayerName(currentBidderId);
            const msg = value === 0 ? t('playerPassed', playerName) : t('playerBid', playerName, value);
            showMessage(msg, 'success');
        } else {
            showMessage(data.error, 'error');
        }
    } catch (error) {
        showMessage(t('failedToPlaceBid', error.message), 'error');
    }
}

async function pickUpTalon() {
    if (!gameState) return;

    const declarerId = gameState.current_round?.declarer_id;
    if (!declarerId) return;

    try {
        const response = await fetch('/api/game/talon', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ player_id: declarerId })
        });
        const data = await response.json();

        if (data.success) {
            gameState = data.state;
            renderGame();
            showMessage(t('talonPickedUp'), 'success');
            // Hide pickup button, show discard section
            document.getElementById('pickup-talon-btn').classList.add('hidden');
        } else {
            showMessage(data.error, 'error');
        }
    } catch (error) {
        showMessage(t('failedToPickUpTalon', error.message), 'error');
    }
}

async function discardSelected() {
    if (!gameState || selectedCards.length !== 2) return;

    const declarerId = gameState.current_round?.declarer_id;
    if (!declarerId) return;

    try {
        const response = await fetch('/api/game/discard', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                player_id: declarerId,
                card_ids: selectedCards
            })
        });
        const data = await response.json();

        if (data.success) {
            gameState = data.state;
            selectedCards = [];
            renderGame();
            showMessage(t('cardsDiscarded'), 'success');
        } else {
            showMessage(data.error, 'error');
        }
    } catch (error) {
        showMessage(t('failedToDiscard', error.message), 'error');
    }
}

async function announceContract() {
    if (!gameState) return;

    const declarerId = gameState.current_round?.declarer_id;
    if (!declarerId) return;

    const contractType = document.getElementById('contract-type').value;
    const trumpSuit = contractType === 'suit' ? document.getElementById('trump-suit').value : null;

    try {
        const response = await fetch('/api/game/contract', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                player_id: declarerId,
                type: contractType,
                trump_suit: trumpSuit
            })
        });
        const data = await response.json();

        if (data.success) {
            gameState = data.state;
            renderGame();
            const contractName = t(contractType);
            const msg = trumpSuit
                ? t('contractAnnouncedWithSuit', contractName, t('suits.' + trumpSuit))
                : t('contractAnnounced', contractName);
            showMessage(msg, 'success');
        } else {
            showMessage(data.error, 'error');
        }
    } catch (error) {
        showMessage(t('failedToAnnounceContract', error.message), 'error');
    }
}

async function playCard(cardId) {
    if (!gameState) return;

    const currentPlayerId = gameState.current_player_id;
    if (!currentPlayerId) return;

    try {
        const response = await fetch('/api/game/play', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                player_id: currentPlayerId,
                card_id: cardId
            })
        });
        const data = await response.json();

        if (data.success) {
            gameState = data.state;
            renderGame();

            if (data.result.trick_complete) {
                const winnerName = getPlayerName(data.result.trick_winner_id);
                showMessage(t('trickWonBy', winnerName), 'success');
            }
            if (data.result.round_complete) {
                showMessage(t('roundComplete'), 'success');
            }
        } else {
            showMessage(data.error, 'error');
        }
    } catch (error) {
        showMessage(t('failedToPlayCard', error.message), 'error');
    }
}

async function nextRound() {
    try {
        const response = await fetch('/api/game/next-round', { method: 'POST' });
        const data = await response.json();

        if (data.success) {
            gameState = data.state;
            selectedCards = [];
            renderGame();
            showMessage(t('newRoundStarted'), 'success');
        } else {
            showMessage(data.error, 'error');
        }
    } catch (error) {
        showMessage(t('failedToStartNextRound', error.message), 'error');
    }
}

function toggleCardSelection(cardId) {
    const idx = selectedCards.indexOf(cardId);
    if (idx >= 0) {
        selectedCards.splice(idx, 1);
    } else if (selectedCards.length < 2) {
        selectedCards.push(cardId);
    }
    renderGame();
    updateDiscardButton();
}

function updateDiscardButton() {
    const discardBtn = document.getElementById('discard-btn');
    discardBtn.disabled = selectedCards.length !== 2;
}

function updateTrumpVisibility() {
    const contractType = document.getElementById('contract-type').value;
    const trumpSelect = document.getElementById('trump-suit');
    trumpSelect.style.display = contractType === 'suit' ? 'block' : 'none';
}

function getPlayerName(playerId) {
    const player = gameState?.players?.find(p => p.id === playerId);
    return player ? player.name : t('player') + ' ' + playerId;
}

// === Rendering Functions ===

function renderGame() {
    if (!gameState) return;

    const round = gameState.current_round;
    const phase = round?.phase || 'waiting';

    // Update phase indicator
    elements.phaseIndicator.textContent = t('phases.' + phase);

    // Render players
    renderPlayers();

    // Render center area
    renderTalon();
    renderCurrentTrick();
    renderContractInfo();

    // Show appropriate action panel
    hideAllActionPanels();
    showActionPanelForPhase(phase);
}

function renderPlayers() {
    const players = gameState.players || [];
    const currentPlayerId = gameState.current_player_id;
    const currentBidderId = gameState.current_round?.auction?.current_bidder_id;
    const declarerId = gameState.current_round?.declarer_id;
    const phase = gameState.current_round?.phase;

    players.forEach(player => {
        const playerEl = document.getElementById(`player${player.id}`);
        if (!playerEl) return;

        // Update player info
        playerEl.querySelector('.player-name').textContent = player.name;
        playerEl.querySelector('.score-value').textContent = player.score;
        playerEl.querySelector('.tricks-value').textContent = player.tricks_won;

        // Role
        const roleEl = playerEl.querySelector('.player-role');
        if (player.is_declarer) {
            roleEl.textContent = t('declarer');
        } else if (player.id === declarerId) {
            roleEl.textContent = t('declarer');
        } else {
            roleEl.textContent = '';
        }

        // Active state
        playerEl.classList.remove('active', 'declarer');
        if (phase === 'auction' && player.id === currentBidderId) {
            playerEl.classList.add('active');
        } else if (phase === 'playing' && player.id === currentPlayerId) {
            playerEl.classList.add('active');
        }
        if (player.is_declarer) {
            playerEl.classList.add('declarer');
        }

        // Render cards
        renderPlayerCards(player, playerEl);
    });
}

function renderPlayerCards(player, playerEl) {
    const cardsContainer = playerEl.querySelector('.player-cards');
    cardsContainer.innerHTML = '';

    const phase = gameState.current_round?.phase;
    const isExchanging = phase === 'exchanging';
    const isPlaying = phase === 'playing';
    const declarerId = gameState.current_round?.declarer_id;
    const isCurrentPlayer = player.id === gameState.current_player_id;
    const legalCards = gameState.legal_cards || [];
    const legalCardIds = legalCards.map(c => c.id);

    player.hand.forEach(card => {
        const img = document.createElement('img');
        img.src = `/api/cards/${card.id}/image`;
        img.alt = card.id;
        img.className = 'card';
        img.title = formatCardName(card.id);
        img.dataset.cardId = card.id;

        // Handle card selection for discarding
        if (isExchanging && player.id === declarerId && player.hand.length === 12) {
            img.classList.add('selectable');
            if (selectedCards.includes(card.id)) {
                img.classList.add('selected');
            }
            img.addEventListener('click', () => toggleCardSelection(card.id));
        }

        // Handle card playing
        if (isPlaying && isCurrentPlayer && legalCardIds.includes(card.id)) {
            img.classList.add('playable');
            img.addEventListener('click', () => playCard(card.id));
        }

        cardsContainer.appendChild(img);
    });
}

function renderTalon() {
    const talonContainer = elements.talon.querySelector('.talon-cards');
    talonContainer.innerHTML = '';

    const round = gameState.current_round;
    if (!round) return;

    const talonCount = round.talon_count || 0;

    // Show card backs for talon
    for (let i = 0; i < talonCount; i++) {
        const img = document.createElement('img');
        img.src = '/api/styles/classic/back';
        img.alt = t('talonCard');
        img.className = 'card';
        talonContainer.appendChild(img);
    }

    // Hide talon section if no cards
    elements.talon.style.display = talonCount > 0 ? 'flex' : 'none';
}

function renderCurrentTrick() {
    const trickContainer = elements.currentTrick.querySelector('.trick-cards');
    trickContainer.innerHTML = '';

    const round = gameState.current_round;
    if (!round || !round.tricks || round.tricks.length === 0) return;

    const currentTrick = round.tricks[round.tricks.length - 1];
    if (!currentTrick || !currentTrick.cards) return;

    currentTrick.cards.forEach(cardPlay => {
        const wrapper = document.createElement('div');
        wrapper.className = 'trick-card-wrapper';

        const label = document.createElement('span');
        label.className = 'trick-card-player';
        const playerName = getPlayerName(cardPlay.player_id);
        label.textContent = playerName;

        const img = document.createElement('img');
        img.src = `/api/cards/${cardPlay.card.id}/image`;
        img.alt = cardPlay.card.id;
        img.className = 'card';

        wrapper.appendChild(label);
        wrapper.appendChild(img);
        trickContainer.appendChild(wrapper);
    });
}

function renderContractInfo() {
    const contract = gameState.current_round?.contract;

    if (contract) {
        const contractName = t(contract.type);
        let text = t('contract') + ': ' + contractName;
        if (contract.trump_suit) {
            text += ` (${t('suits.' + contract.trump_suit)})`;
        }
        text += ' - ' + t('needTricks', contract.tricks_required);
        elements.contractInfo.textContent = text;
        elements.contractInfo.style.display = 'block';
    } else {
        elements.contractInfo.style.display = 'none';
    }
}

function hideAllActionPanels() {
    elements.biddingControls.classList.add('hidden');
    elements.exchangeControls.classList.add('hidden');
    elements.contractControls.classList.add('hidden');
    elements.playControls.classList.add('hidden');
    elements.scoringControls.classList.add('hidden');
}

function showActionPanelForPhase(phase) {
    const round = gameState.current_round;
    const declarerId = round?.declarer_id;

    switch (phase) {
        case 'auction':
            elements.biddingControls.classList.remove('hidden');
            updateBiddingButtons();
            break;

        case 'exchanging':
            const declarer = gameState.players?.find(p => p.id === declarerId);
            if (declarer && declarer.hand.length === 10) {
                // Already discarded, show contract controls
                elements.contractControls.classList.remove('hidden');
                updateTrumpVisibility();
            } else {
                // Show exchange controls
                elements.exchangeControls.classList.remove('hidden');
                const pickupBtn = document.getElementById('pickup-talon-btn');
                pickupBtn.classList.toggle('hidden', declarer && declarer.hand.length === 12);
            }
            break;

        case 'playing':
            elements.playControls.classList.remove('hidden');
            break;

        case 'scoring':
            elements.scoringControls.classList.remove('hidden');
            showRoundResult();
            break;
    }
}

function updateBiddingButtons() {
    const auction = gameState.current_round?.auction;
    const highestBid = auction?.highest_bid;
    const minBid = highestBid && !highestBid.is_pass ? highestBid.value + 1 : 2;

    document.querySelectorAll('.bid-btn').forEach(btn => {
        const value = parseInt(btn.dataset.value);
        if (value === 0) {
            btn.disabled = false; // Can always pass
        } else {
            btn.disabled = value < minBid;
        }
    });
}

function showRoundResult() {
    const players = gameState.players || [];
    const declarerId = gameState.current_round?.declarer_id;
    const declarer = players.find(p => p.id === declarerId);

    if (declarer) {
        const result = document.getElementById('round-result');
        result.textContent = t('roundOver', declarer.name, declarer.tricks_won);
    }
}

function showMessage(text, type = '') {
    elements.messageArea.textContent = text;
    elements.messageArea.className = 'message-area';
    if (type) {
        elements.messageArea.classList.add(type);
    }
}

function formatCardName(cardId) {
    const [rank, suit] = cardId.split('_');
    const rankName = t('ranks.' + rank);
    const suitName = t('suits.' + suit);
    return t('cardName', rankName, suitName);
}
