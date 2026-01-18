// Preferans Game Client

let gameState = null;
let selectedCards = [];

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
    if (elements.languageSelector && window.i18n) {
        elements.languageSelector.value = window.i18n.getLanguage();
        elements.languageSelector.addEventListener('change', (e) => {
            window.i18n.setLanguage(e.target.value);
            if (gameState) {
                renderGame();
            }
        });
    }

    // Bidding buttons are now dynamically generated

    // Exchange buttons
    document.getElementById('pickup-talon-btn').addEventListener('click', pickUpTalon);
    document.getElementById('discard-btn').addEventListener('click', discardSelected);

    // Contract controls
    document.getElementById('announce-btn').addEventListener('click', announceContract);

    // Next round button
    document.getElementById('next-round-btn').addEventListener('click', nextRound);

    // Play area drop zone
    const playArea = document.getElementById('play-area');
    if (playArea) {
        playArea.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            playArea.classList.add('drag-over');
        });

        playArea.addEventListener('dragleave', (e) => {
            // Only remove if leaving the play area entirely
            if (!playArea.contains(e.relatedTarget)) {
                playArea.classList.remove('drag-over');
            }
        });

        playArea.addEventListener('drop', (e) => {
            e.preventDefault();
            playArea.classList.remove('drag-over');
            const cardId = e.dataTransfer.getData('text/plain');
            if (cardId) {
                playCard(cardId);
            }
        });
    }

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
            showMessage(data.error || 'Unknown error', 'error');
        }
    } catch (error) {
        showMessage(t('failedToStartGame', error.message), 'error');
    }
}

async function placeBid(bidType, value) {
    if (!gameState) return;

    const currentBidderId = gameState.current_round?.auction?.current_bidder_id;
    if (!currentBidderId) return;

    try {
        const response = await fetch('/api/game/bid', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                player_id: currentBidderId,
                bid_type: bidType,
                value: value
            })
        });
        const data = await response.json();

        if (data.success) {
            gameState = data.state;
            console.log('[placeBid] Bid successful. New game state:', gameState);
            console.log('[placeBid] Phase:', gameState.current_round?.phase);
            console.log('[placeBid] Auction phase:', gameState.auction_phase);
            console.log('[placeBid] Declarer:', gameState.current_round?.declarer_id);
            console.log('[placeBid] Contract:', gameState.current_round?.contract);
            console.log('[placeBid] legal_contract_levels:', gameState.legal_contract_levels);
            renderGame();
            const playerName = getPlayerName(currentBidderId);
            const bidText = getBidDescription(bidType, value);
            showMessage(`${playerName} ${bidText}`, 'success');
        } else {
            showMessage(data.error, 'error');
        }
    } catch (error) {
        showMessage(t('failedToPlaceBid', error.message), 'error');
    }
}

function getBidDescription(bidType, value) {
    if (bidType === 'pass') return t('playerPassed', '').replace('{0} ', '');
    if (bidType === 'game') return `${t('playerBid', '', value).replace('{0} ', '')}`;
    if (bidType === 'in_hand') return value > 0 ? `declared In Hand ${value}` : 'declared In Hand';
    if (bidType === 'betl') return 'bid Betl';
    if (bidType === 'sans') return 'bid Sans';
    return `bid ${value}`;
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

    const levelSelect = document.getElementById('contract-level');
    const selectedLevel = parseInt(levelSelect.value, 10);

    if (selectedLevel < 2 || selectedLevel > 7) {
        showMessage('Invalid contract selection', 'error');
        return;
    }

    try {
        const response = await fetch('/api/game/contract', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                player_id: declarerId,
                level: selectedLevel
            })
        });
        const data = await response.json();

        if (data.success) {
            gameState = data.state;
            renderGame();
            const contractName = selectedLevel === 6 ? t('betl') :
                                 selectedLevel === 7 ? t('sans') :
                                 t('game') + ' ' + selectedLevel;
            showMessage(t('contractAnnounced', contractName), 'success');
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

function populateContractOptions() {
    const levelSelect = document.getElementById('contract-level');
    levelSelect.innerHTML = '';

    // Use legal_contract_levels from game state if available (set by backend)
    const legalLevels = gameState.legal_contract_levels;

    console.log('[populateContractOptions] legal_contract_levels:', legalLevels);

    if (legalLevels && legalLevels.length > 0) {
        // Use the levels provided by the backend
        legalLevels.forEach(level => {
            if (level === 6) {
                addOption(levelSelect, '6', `6 (${t('betl')})`);
            } else if (level === 7) {
                addOption(levelSelect, '7', `7 (${t('sans')})`);
            } else {
                addOption(levelSelect, level.toString(), level.toString());
            }
        });
        return;
    }

    // Fallback: calculate from auction (for regular games during exchanging phase)
    const auction = gameState.current_round?.auction;
    const winnerBid = auction?.highest_game_bid || auction?.highest_in_hand_bid;

    // Determine minimum level from winning bid
    let minLevel = 2;
    if (winnerBid) {
        const bidValue = winnerBid.effective_value || winnerBid.value || 2;
        minLevel = Math.max(2, Math.min(bidValue, 7));
    }

    console.log('[populateContractOptions] fallback minLevel:', minLevel);

    // Add level options starting from minimum (2-7)
    for (let level = minLevel; level <= 5; level++) {
        addOption(levelSelect, level.toString(), level.toString());
    }

    // Add Betl (6) if minimum allows
    if (minLevel <= 6) {
        addOption(levelSelect, '6', `6 (${t('betl')})`);
    }

    // Add Sans (7) if minimum allows
    if (minLevel <= 7) {
        addOption(levelSelect, '7', `7 (${t('sans')})`);
    }
}

function addOption(select, value, label) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    select.appendChild(option);
}

function getPlayerName(playerId) {
    const player = gameState?.players?.find(p => p.id === playerId);
    return player ? getTranslatedPlayerName(player) : t('player') + ' ' + playerId;
}

// === Rendering Functions ===

function renderGame() {
    if (!gameState) return;

    const round = gameState.current_round;
    const phase = round?.phase || 'waiting';
    const auctionPhase = gameState.auction_phase;

    // Update phase indicator
    let phaseText = t('phases.' + phase);
    if (phase === 'auction' && auctionPhase) {
        const auctionPhaseKeys = {
            'initial': 'phases.auction',
            'game_bidding': 'phases.auction',
            'in_hand_deciding': 'phases.auction',
            'in_hand_declaring': 'phases.auction',
            'complete': 'phases.auction'
        };
        phaseText = t(auctionPhaseKeys[auctionPhase] || 'phases.auction');
    }
    elements.phaseIndicator.textContent = phaseText;

    // Render players
    renderPlayers();

    // Render center area
    renderTalon();
    renderCurrentTrick();
    renderLastTrick();
    renderContractInfo();
    renderBiddingHistory();

    // Show/hide drop hint based on phase
    const playArea = document.getElementById('play-area');
    if (playArea) {
        const isPlaying = phase === 'playing';
        const hasLegalCards = (gameState.legal_cards || []).length > 0;
        const trickCards = gameState.current_round?.tricks?.slice(-1)[0]?.cards || [];
        const showHint = isPlaying && hasLegalCards && trickCards.length === 0;
        playArea.classList.toggle('show-drop-hint', showHint);
    }

    // Show appropriate action panel
    hideAllActionPanels();
    showActionPanelForPhase(phase);
}

function getTranslatedPlayerName(player) {
    // Use translated default names for Player 1/2/3
    // This allows language switching to update player names
    const defaultNames = ['Player 1', 'Player 2', 'Player 3', 'Igrac 1', 'Igrac 2', 'Igrac 3'];
    if (defaultNames.includes(player.name) || player.name === t('player' + player.id)) {
        return t('player' + player.id);
    }
    return player.name;
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

        // Update player info - use translated name
        playerEl.querySelector('.player-name').textContent = getTranslatedPlayerName(player);
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

        // Handle card playing (click or drag)
        if (isPlaying && isCurrentPlayer && legalCardIds.includes(card.id)) {
            img.classList.add('playable');
            img.draggable = true;
            img.addEventListener('click', () => playCard(card.id));

            // Drag events
            img.addEventListener('dragstart', (e) => {
                e.dataTransfer.setData('text/plain', card.id);
                e.dataTransfer.effectAllowed = 'move';
                img.classList.add('dragging');
                const playArea = document.getElementById('play-area');
                if (playArea) playArea.classList.add('drag-over');
            });

            img.addEventListener('dragend', () => {
                img.classList.remove('dragging');
                const playArea = document.getElementById('play-area');
                if (playArea) playArea.classList.remove('drag-over');
            });
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

function renderLastTrick() {
    const lastTrickEl = document.getElementById('last-trick');
    const cardsContainer = lastTrickEl.querySelector('.last-trick-cards');
    cardsContainer.innerHTML = '';

    const round = gameState.current_round;
    if (!round || !round.tricks || round.tricks.length < 2) {
        // No completed trick yet (need at least 2 tricks - one complete, one in progress)
        // Or if only 1 trick and it's complete, show it
        if (round?.tricks?.length === 1 && round.tricks[0].cards?.length === 3) {
            // First trick is complete, show it
        } else {
            lastTrickEl.classList.remove('visible');
            return;
        }
    }

    // Find the last completed trick (one with 3 cards that's not the current trick)
    let lastCompletedTrick = null;
    for (let i = round.tricks.length - 1; i >= 0; i--) {
        const trick = round.tricks[i];
        if (trick.cards && trick.cards.length === 3) {
            // This is a completed trick
            // If it's the last trick and there are more tricks, or if there's only one trick
            if (i < round.tricks.length - 1 || round.tricks.length === 1 ||
                (i === round.tricks.length - 1 && trick.cards.length === 3)) {
                lastCompletedTrick = trick;
                break;
            }
        }
    }

    // If the current trick has 3 cards, it's actually the last completed trick
    const currentTrick = round.tricks[round.tricks.length - 1];
    if (currentTrick?.cards?.length === 3) {
        lastCompletedTrick = currentTrick;
    } else if (round.tricks.length >= 2) {
        // The previous trick is the last completed one
        lastCompletedTrick = round.tricks[round.tricks.length - 2];
    }

    if (!lastCompletedTrick || !lastCompletedTrick.cards || lastCompletedTrick.cards.length !== 3) {
        lastTrickEl.classList.remove('visible');
        return;
    }

    lastTrickEl.classList.add('visible');

    lastCompletedTrick.cards.forEach(cardPlay => {
        const wrapper = document.createElement('div');
        wrapper.className = 'trick-card-wrapper';

        const label = document.createElement('span');
        label.className = 'trick-card-player';
        if (cardPlay.player_id === lastCompletedTrick.winner_id) {
            label.classList.add('trick-card-winner');
        }
        const playerName = getPlayerName(cardPlay.player_id);
        // Shorten player name for compact display
        label.textContent = playerName.split(' ')[0].substring(0, 3);

        const img = document.createElement('img');
        img.src = `/api/cards/${cardPlay.card.id}/image`;
        img.alt = cardPlay.card.id;
        img.className = 'card';

        wrapper.appendChild(label);
        wrapper.appendChild(img);
        cardsContainer.appendChild(wrapper);
    });
}

function renderContractInfo() {
    const contract = gameState.current_round?.contract;

    console.log('[renderContractInfo] contract:', contract);

    if (contract) {
        const contractName = t(contract.type);
        let text = t('contract') + ': ';

        // Show level for suit contracts (2-5)
        if (contract.type === 'suit' && contract.bid_value >= 2 && contract.bid_value <= 5) {
            text += `${t('game')} ${contract.bid_value}`;
            if (contract.trump_suit) {
                text += ` (${t('suits.' + contract.trump_suit)})`;
            }
        } else {
            text += contractName;
            if (contract.trump_suit) {
                text += ` (${t('suits.' + contract.trump_suit)})`;
            }
        }

        // Show if in_hand
        if (contract.is_in_hand) {
            text += ` [${t('inHand')}]`;
        }

        text += ' - ' + t('needTricks', contract.tricks_required);
        elements.contractInfo.textContent = text;
        elements.contractInfo.style.display = 'block';
    } else {
        elements.contractInfo.style.display = 'none';
    }
}

function renderBiddingHistory() {
    const historyContainer = document.getElementById('bidding-history');
    const historyList = document.getElementById('bidding-history-list');

    if (!historyContainer || !historyList) return;

    const auction = gameState.current_round?.auction;
    const phase = gameState.current_round?.phase;

    // Show bidding history during auction and briefly after
    if (!auction || !auction.bids || auction.bids.length === 0) {
        historyContainer.style.display = 'none';
        return;
    }

    historyContainer.style.display = 'block';
    historyList.innerHTML = '';

    // Render each bid on a separate line
    auction.bids.forEach(bid => {
        const bidLine = document.createElement('div');
        bidLine.className = 'bid-line';

        const playerName = getPlayerName(bid.player_id);
        const bidText = formatBidForHistory(bid);

        bidLine.innerHTML = `<span class="bid-player">${playerName}:</span> <span class="bid-value">${bidText}</span>`;

        // Add styling based on bid type
        if (bid.is_pass) {
            bidLine.classList.add('bid-pass');
        } else if (bid.bid_type === 'in_hand') {
            bidLine.classList.add('bid-in-hand');
        } else if (bid.bid_type === 'betl' || bid.bid_type === 'sans') {
            bidLine.classList.add('bid-special');
        }

        historyList.appendChild(bidLine);
    });

    // Auto-scroll to bottom
    historyList.scrollTop = historyList.scrollHeight;
}

function formatBidForHistory(bid) {
    if (bid.is_pass || bid.bid_type === 'pass') {
        return t('pass');
    }
    if (bid.bid_type === 'game') {
        return bid.value.toString();
    }
    if (bid.bid_type === 'in_hand') {
        if (bid.value > 0) {
            return t('inHand') + ' ' + bid.value;
        }
        return t('inHand');
    }
    if (bid.bid_type === 'betl') {
        return t('betl');
    }
    if (bid.bid_type === 'sans') {
        return t('sans');
    }
    return bid.effective_value?.toString() || '?';
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

    // Debug logging for in_hand contract selection
    console.log('[showActionPanelForPhase] phase:', phase);
    console.log('[showActionPanelForPhase] declarerId:', declarerId);
    console.log('[showActionPanelForPhase] contract:', round?.contract);
    console.log('[showActionPanelForPhase] legal_contract_levels:', gameState.legal_contract_levels);

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
                populateContractOptions();
            } else {
                // Show exchange controls
                elements.exchangeControls.classList.remove('hidden');
                const pickupBtn = document.getElementById('pickup-talon-btn');
                pickupBtn.classList.toggle('hidden', declarer && declarer.hand.length === 12);
            }
            break;

        case 'playing':
            // Check if contract needs to be selected first (in_hand games)
            if (gameState.legal_contract_levels && gameState.legal_contract_levels.length > 0 && !round?.contract) {
                console.log('[showActionPanelForPhase] In_hand game: showing contract controls for level selection');
                elements.contractControls.classList.remove('hidden');
                populateContractOptions();
            } else {
                elements.playControls.classList.remove('hidden');
            }
            break;

        case 'scoring':
            elements.scoringControls.classList.remove('hidden');
            showRoundResult();
            break;
    }
}

function translateBidLabel(bid) {
    // Translate bid labels from server to current language
    if (bid.bid_type === 'pass') return t('pass');
    if (bid.bid_type === 'game') return t('game') + ' ' + bid.value;
    if (bid.bid_type === 'in_hand') return bid.value > 0 ? t('inHand') + ' ' + bid.value : t('inHand');
    if (bid.bid_type === 'betl') return t('betl');
    if (bid.bid_type === 'sans') return t('sans');
    // Check for "Hold" prefix
    if (bid.label && bid.label.startsWith('Hold')) return t('hold') + ' ' + bid.value;
    return bid.label;
}

function updateBiddingButtons() {
    const legalBids = gameState.legal_bids || [];
    const container = document.getElementById('bid-buttons');
    container.innerHTML = '';

    if (legalBids.length === 0) {
        container.innerHTML = `<span class="action-label">${t('clickCardToPlay')}</span>`;
        return;
    }

    legalBids.forEach(bid => {
        const btn = document.createElement('button');
        btn.className = 'bid-btn';
        btn.textContent = translateBidLabel(bid);
        btn.dataset.bidType = bid.bid_type;
        btn.dataset.value = bid.value;

        // Style pass button differently
        if (bid.bid_type === 'pass') {
            btn.classList.add('pass-btn');
        }
        // Style special bids
        if (bid.bid_type === 'in_hand') {
            btn.classList.add('in-hand-btn');
        }
        if (bid.bid_type === 'betl' || bid.bid_type === 'sans') {
            btn.classList.add('special-btn');
        }

        btn.addEventListener('click', () => placeBid(bid.bid_type, bid.value));
        container.appendChild(btn);
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
