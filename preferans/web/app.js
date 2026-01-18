// Preferans Game Client
console.log('=== APP.JS LOADED ===', new Date().toISOString());

let gameState = null;
let selectedCards = [];

// Debug logging utility
const DEBUG = true;
function debug(category, message, data = null) {
    if (!DEBUG) return;
    const timestamp = new Date().toISOString().substr(11, 12);
    const prefix = `[${timestamp}] [${category}]`;
    if (data !== null) {
        console.log(prefix, message, data);
    } else {
        console.log(prefix, message);
    }
}

function debugError(category, message, error = null) {
    const timestamp = new Date().toISOString().substr(11, 12);
    const prefix = `[${timestamp}] [${category}] ERROR:`;
    if (error) {
        console.error(prefix, message, error);
    } else {
        console.error(prefix, message);
    }
}

function debugWarn(category, message, data = null) {
    const timestamp = new Date().toISOString().substr(11, 12);
    const prefix = `[${timestamp}] [${category}] WARN:`;
    if (data !== null) {
        console.warn(prefix, message, data);
    } else {
        console.warn(prefix, message);
    }
}

// Validate game state integrity
function validateGameState(context = 'unknown') {
    if (!gameState) {
        debugWarn('STATE', `validateGameState(${context}): gameState is null`);
        return false;
    }

    if (!gameState.players || !Array.isArray(gameState.players)) {
        debugError('STATE', `validateGameState(${context}): players missing or invalid`, gameState);
        return false;
    }

    if (gameState.players.length !== 3) {
        debugError('STATE', `validateGameState(${context}): expected 3 players, got ${gameState.players.length}`);
        return false;
    }

    if (!gameState.current_round) {
        debugWarn('STATE', `validateGameState(${context}): no current_round`);
        // This might be OK at game start
    }

    debug('STATE', `validateGameState(${context}): OK`, {
        phase: gameState.current_round?.phase,
        players: gameState.players.map(p => ({ id: p.id, hand: p.hand?.length }))
    });

    return true;
}

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
    debug('INIT', 'DOMContentLoaded - initializing app');

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

    // Verify all required DOM elements exist
    const missingElements = Object.entries(elements)
        .filter(([key, el]) => !el)
        .map(([key]) => key);

    if (missingElements.length > 0) {
        debugError('INIT', 'Missing DOM elements:', missingElements);
    } else {
        debug('INIT', 'All DOM elements found');
    }

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
    debug('API', 'checkServer: checking server health');
    try {
        const response = await fetch('/api/health');
        if (!response.ok) {
            debugError('API', `checkServer: HTTP error ${response.status}`);
            showMessage(t('serverConnectionFailed'), 'error');
            return;
        }
        const data = await response.json();
        debug('API', 'checkServer: server healthy', data);
        showMessage(t('serverStatus', data.status), 'success');
    } catch (error) {
        debugError('API', 'checkServer: failed', error);
        showMessage(t('serverConnectionFailed'), 'error');
    }
}

async function startNewGame() {
    debug('GAME', 'startNewGame: initiating new game');
    try {
        showMessage(t('startingNewGame'));
        const response = await fetch('/api/game/new', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                players: [t('player1'), t('player2'), t('player3')]
            })
        });

        if (!response.ok) {
            debugError('API', `startNewGame: HTTP error ${response.status}`);
            showMessage(`Server error: ${response.status}`, 'error');
            return;
        }

        const data = await response.json();
        debug('API', 'startNewGame: response received', { success: data.success, hasState: !!data.state });

        if (data.success) {
            if (!data.state) {
                debugError('GAME', 'startNewGame: success but no state returned');
                showMessage('Server returned invalid state', 'error');
                return;
            }
            gameState = data.state;
            selectedCards = [];
            validateGameState('startNewGame');
            debug('GAME', 'startNewGame: game started successfully', {
                phase: gameState.current_round?.phase,
                playerCount: gameState.players?.length
            });
            renderGame();
            showMessage(t('gameStarted'), 'success');
        } else {
            debugError('GAME', 'startNewGame: failed', data.error);
            showMessage(data.error || 'Unknown error', 'error');
        }
    } catch (error) {
        debugError('API', 'startNewGame: exception', error);
        showMessage(t('failedToStartGame', error.message), 'error');
    }
}

async function placeBid(bidType, value) {
    debug('BID', `placeBid: type=${bidType}, value=${value}`);

    if (!gameState) {
        debugError('BID', 'placeBid: no game state');
        return;
    }

    const currentBidderId = gameState.current_round?.auction?.current_bidder_id;
    if (!currentBidderId) {
        debugError('BID', 'placeBid: no current bidder', {
            hasRound: !!gameState.current_round,
            hasAuction: !!gameState.current_round?.auction
        });
        return;
    }

    debug('BID', `placeBid: bidder=${currentBidderId}`);

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

        if (!response.ok) {
            debugError('API', `placeBid: HTTP error ${response.status}`);
            showMessage(`Server error: ${response.status}`, 'error');
            return;
        }

        const data = await response.json();
        debug('API', 'placeBid: response', { success: data.success });

        if (data.success) {
            if (!data.state) {
                debugError('BID', 'placeBid: success but no state');
                return;
            }
            gameState = data.state;
            debug('BID', 'placeBid: auction phase after bid', {
                phase: gameState.current_round?.auction?.phase,
                nextBidder: gameState.current_round?.auction?.current_bidder_id
            });
            renderGame();
            const playerName = getPlayerName(currentBidderId);
            const bidText = getBidDescription(bidType, value);
            showMessage(`${playerName} ${bidText}`, 'success');
        } else {
            debugError('BID', 'placeBid: failed', data.error);
            showMessage(data.error, 'error');
        }
    } catch (error) {
        debugError('API', 'placeBid: exception', error);
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
    debug('EXCHANGE', 'pickUpTalon: attempting to pick up talon');

    if (!gameState) {
        debugError('EXCHANGE', 'pickUpTalon: no game state');
        return;
    }

    const declarerId = gameState.current_round?.declarer_id;
    if (!declarerId) {
        debugError('EXCHANGE', 'pickUpTalon: no declarer', {
            hasRound: !!gameState.current_round
        });
        return;
    }

    debug('EXCHANGE', `pickUpTalon: declarer=${declarerId}`);

    try {
        const response = await fetch('/api/game/talon', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ player_id: declarerId })
        });

        if (!response.ok) {
            debugError('API', `pickUpTalon: HTTP error ${response.status}`);
            showMessage(`Server error: ${response.status}`, 'error');
            return;
        }

        const data = await response.json();
        debug('API', 'pickUpTalon: response', { success: data.success });

        if (data.success) {
            if (!data.state) {
                debugError('EXCHANGE', 'pickUpTalon: success but no state');
                return;
            }
            gameState = data.state;
            const declarer = gameState.players?.find(p => p.id === declarerId);
            debug('EXCHANGE', 'pickUpTalon: success', {
                declarerHandSize: declarer?.hand?.length
            });
            renderGame();
            showMessage(t('talonPickedUp'), 'success');
            // Hide pickup button, show discard section
            document.getElementById('pickup-talon-btn').classList.add('hidden');
        } else {
            debugError('EXCHANGE', 'pickUpTalon: failed', data.error);
            showMessage(data.error, 'error');
        }
    } catch (error) {
        debugError('API', 'pickUpTalon: exception', error);
        showMessage(t('failedToPickUpTalon', error.message), 'error');
    }
}

async function discardSelected() {
    debug('EXCHANGE', 'discardSelected: attempting to discard', { selectedCards });

    if (!gameState) {
        debugError('EXCHANGE', 'discardSelected: no game state');
        return;
    }

    if (selectedCards.length !== 2) {
        debugWarn('EXCHANGE', `discardSelected: need 2 cards, have ${selectedCards.length}`);
        return;
    }

    const declarerId = gameState.current_round?.declarer_id;
    if (!declarerId) {
        debugError('EXCHANGE', 'discardSelected: no declarer');
        return;
    }

    debug('EXCHANGE', `discardSelected: declarer=${declarerId}, cards=${selectedCards.join(',')}`);

    try {
        const response = await fetch('/api/game/discard', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                player_id: declarerId,
                card_ids: selectedCards
            })
        });

        if (!response.ok) {
            debugError('API', `discardSelected: HTTP error ${response.status}`);
            showMessage(`Server error: ${response.status}`, 'error');
            return;
        }

        const data = await response.json();
        debug('API', 'discardSelected: response', { success: data.success });

        if (data.success) {
            if (!data.state) {
                debugError('EXCHANGE', 'discardSelected: success but no state');
                return;
            }
            gameState = data.state;
            const declarer = gameState.players?.find(p => p.id === declarerId);
            debug('EXCHANGE', 'discardSelected: success', {
                declarerHandSize: declarer?.hand?.length
            });
            selectedCards = [];
            renderGame();
            showMessage(t('cardsDiscarded'), 'success');
        } else {
            debugError('EXCHANGE', 'discardSelected: failed', data.error);
            showMessage(data.error, 'error');
        }
    } catch (error) {
        debugError('API', 'discardSelected: exception', error);
        showMessage(t('failedToDiscard', error.message), 'error');
    }
}

async function announceContract() {
    debug('CONTRACT', 'announceContract: starting');

    if (!gameState) {
        debugError('CONTRACT', 'announceContract: no game state');
        return;
    }

    const declarerId = gameState.current_round?.declarer_id;
    if (!declarerId) {
        debugError('CONTRACT', 'announceContract: no declarer');
        return;
    }

    const levelSelect = document.getElementById('contract-level');
    if (!levelSelect) {
        debugError('CONTRACT', 'announceContract: contract-level select not found');
        return;
    }

    const selectedLevel = parseInt(levelSelect.value, 10);
    debug('CONTRACT', `announceContract: level=${selectedLevel}, declarer=${declarerId}`);

    if (isNaN(selectedLevel) || selectedLevel < 2 || selectedLevel > 7) {
        debugError('CONTRACT', `announceContract: invalid level ${levelSelect.value}`);
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

        if (!response.ok) {
            debugError('API', `announceContract: HTTP error ${response.status}`);
            showMessage(`Server error: ${response.status}`, 'error');
            return;
        }

        const data = await response.json();
        debug('API', 'announceContract: response', { success: data.success });

        if (data.success) {
            if (!data.state) {
                debugError('CONTRACT', 'announceContract: success but no state');
                return;
            }
            gameState = data.state;
            debug('CONTRACT', 'announceContract: success', {
                phase: gameState.current_round?.phase,
                contract: gameState.current_round?.contract
            });
            renderGame();
            const contractName = selectedLevel === 6 ? t('betl') :
                                 selectedLevel === 7 ? t('sans') :
                                 t('game') + ' ' + selectedLevel;
            showMessage(t('contractAnnounced', contractName), 'success');
        } else {
            debugError('CONTRACT', 'announceContract: failed', data.error);
            showMessage(data.error, 'error');
        }
    } catch (error) {
        debugError('API', 'announceContract: exception', error);
        showMessage(t('failedToAnnounceContract', error.message), 'error');
    }
}

async function playCard(cardId) {
    console.log('=== PLAY CARD CALLED ===', cardId);
    debug('PLAY', `playCard: cardId=${cardId}`);

    if (!cardId) {
        debugError('PLAY', 'playCard: no cardId provided');
        return;
    }

    if (!gameState) {
        debugError('PLAY', 'playCard: no game state');
        return;
    }

    const currentPlayerId = gameState.current_player_id;
    if (!currentPlayerId) {
        debugError('PLAY', 'playCard: no current player', {
            phase: gameState.current_round?.phase
        });
        return;
    }

    // Validate card is in legal cards
    const legalCards = gameState.legal_cards || [];
    const isLegal = legalCards.some(c => c.id === cardId);
    if (!isLegal) {
        debugWarn('PLAY', `playCard: card ${cardId} not in legal cards`, legalCards.map(c => c.id));
    }

    debug('PLAY', `playCard: player=${currentPlayerId}, card=${cardId}`, {
        trickNumber: gameState.current_round?.tricks?.length,
        cardsInTrick: gameState.current_round?.tricks?.slice(-1)[0]?.cards?.length || 0
    });

    try {
        const response = await fetch('/api/game/play', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                player_id: currentPlayerId,
                card_id: cardId
            })
        });

        if (!response.ok) {
            debugError('API', `playCard: HTTP error ${response.status}`);
            showMessage(`Server error: ${response.status}`, 'error');
            return;
        }

        const data = await response.json();
        console.log('=== PLAY CARD RESPONSE ===', data);
        console.log('=== TRICK COMPLETE? ===', data.result?.trick_complete);
        debug('API', 'playCard: response', {
            success: data.success,
            trickComplete: data.result?.trick_complete,
            roundComplete: data.result?.round_complete
        });

        if (data.success) {
            if (!data.state) {
                debugError('PLAY', 'playCard: success but no state');
                return;
            }

            if (!data.result) {
                debugError('PLAY', 'playCard: success but no result');
                gameState = data.state;
                renderGame();
                return;
            }

            if (data.result.trick_complete) {
                debug('PLAY', 'playCard: trick complete, starting animation');

                // Trick is complete - show animation before updating state
                const winnerId = data.result.trick_winner_id;
                if (!winnerId) {
                    debugError('PLAY', 'playCard: trick complete but no winner_id');
                    gameState = data.state;
                    renderGame();
                    return;
                }

                const winnerName = getPlayerName(winnerId);
                debug('PLAY', `playCard: trick won by player ${winnerId} (${winnerName})`);

                // Add the just-played card to the display
                const playedCard = data.result.card;
                if (!playedCard) {
                    debugError('PLAY', 'playCard: no card in result');
                    gameState = data.state;
                    renderGame();
                    return;
                }

                addCardToTrickDisplay(currentPlayerId, playedCard);
                console.log('[PLAY] Added 3rd card to display, checking trick container...');

                const checkContainer = elements.currentTrick?.querySelector('.trick-cards');
                console.log('[PLAY] Trick container children:', checkContainer?.children?.length);

                showMessage(t('trickWonBy', winnerName), 'success');

                // Wait 0.5 seconds to show complete trick
                debug('PLAY', 'playCard: waiting 0.5s to show trick');
                console.log('[PLAY] Waiting 0.5 seconds to show complete trick...');
                await new Promise(resolve => setTimeout(resolve, 500));
                console.log('[PLAY] Done waiting, starting animation...');

                // Animate cards to winner's box
                debug('PLAY', 'playCard: starting animation to winner');
                await animateTrickToWinner(winnerId);
                debug('PLAY', 'playCard: animation complete');

                // Now update state and render
                gameState = data.state;
                debug('PLAY', 'playCard: updated state after trick', {
                    trickNumber: gameState.current_round?.tricks?.length,
                    phase: gameState.current_round?.phase
                });
                renderGame();

                if (data.result.round_complete) {
                    debug('PLAY', 'playCard: round complete');
                    showMessage(t('roundComplete'), 'success');
                }
            } else {
                // Normal card play - just update and render
                gameState = data.state;
                debug('PLAY', 'playCard: card played', {
                    cardsInTrick: gameState.current_round?.tricks?.slice(-1)[0]?.cards?.length,
                    nextPlayer: gameState.current_player_id
                });
                renderGame();
            }
        } else {
            debugError('PLAY', 'playCard: failed', data.error);
            showMessage(data.error, 'error');
        }
    } catch (error) {
        debugError('API', 'playCard: exception', error);
        showMessage(t('failedToPlayCard', error.message), 'error');
    }
}

function addCardToTrickDisplay(playerId, card) {
    debug('RENDER', `addCardToTrickDisplay: player=${playerId}, card=${card?.id}`);

    if (!elements.currentTrick) {
        debugError('RENDER', 'addCardToTrickDisplay: currentTrick element not found');
        return;
    }

    const trickContainer = elements.currentTrick.querySelector('.trick-cards');
    if (!trickContainer) {
        debugError('RENDER', 'addCardToTrickDisplay: trick-cards container not found');
        return;
    }

    if (!card || !card.id) {
        debugError('RENDER', 'addCardToTrickDisplay: invalid card', card);
        return;
    }

    const wrapper = document.createElement('div');
    wrapper.className = 'trick-card-wrapper';

    const label = document.createElement('span');
    label.className = 'trick-card-player';
    label.textContent = getPlayerName(playerId);

    const img = document.createElement('img');
    img.src = `/api/cards/${card.id}/image`;
    img.alt = card.id;
    img.className = 'card';
    img.onerror = () => debugError('RENDER', `addCardToTrickDisplay: failed to load image for ${card.id}`);

    wrapper.appendChild(label);
    wrapper.appendChild(img);
    trickContainer.appendChild(wrapper);

    debug('RENDER', `addCardToTrickDisplay: added card, total cards in trick: ${trickContainer.children.length}`);
}

async function animateTrickToWinner(winnerId) {
    debug('ANIM', `animateTrickToWinner: winner=${winnerId}`);
    console.log('[ANIM] Starting animation to winner', winnerId);

    if (!elements.currentTrick) {
        debugError('ANIM', 'animateTrickToWinner: currentTrick element not found');
        console.error('[ANIM] currentTrick element is null!');
        return;
    }

    const trickContainer = elements.currentTrick.querySelector('.trick-cards');
    if (!trickContainer) {
        debugError('ANIM', 'animateTrickToWinner: trick-cards container not found');
        console.error('[ANIM] trick-cards container not found!');
        return;
    }

    console.log('[ANIM] trickContainer innerHTML:', trickContainer.innerHTML);

    const cardWrappers = trickContainer.querySelectorAll('.trick-card-wrapper');
    debug('ANIM', `animateTrickToWinner: found ${cardWrappers.length} cards to animate`);
    console.log('[ANIM] Found card wrappers:', cardWrappers.length);

    if (cardWrappers.length === 0) {
        debugWarn('ANIM', 'animateTrickToWinner: no cards to animate');
        console.warn('[ANIM] No cards found to animate!');
        return;
    }

    // Find winner's player box (specifically the tricks display)
    const winnerEl = document.getElementById(`player${winnerId}`);
    if (!winnerEl) {
        debugError('ANIM', `animateTrickToWinner: winner element player${winnerId} not found`);
        return;
    }

    const targetEl = winnerEl.querySelector('.player-info');
    if (!targetEl) {
        debugError('ANIM', 'animateTrickToWinner: player-info element not found');
        return;
    }

    const targetRect = targetEl.getBoundingClientRect();
    const targetX = targetRect.left + targetRect.width / 2;
    const targetY = targetRect.top + targetRect.height / 2;

    debug('ANIM', `animateTrickToWinner: target position (${targetX.toFixed(0)}, ${targetY.toFixed(0)})`);

    // Animate each card
    console.log('[ANIM] Starting to animate each card...');
    cardWrappers.forEach((wrapper, index) => {
        const rect = wrapper.getBoundingClientRect();
        const startX = rect.left;
        const startY = rect.top;

        console.log(`[ANIM] Card ${index}: rect=`, rect, `startX=${startX}, startY=${startY}`);
        debug('ANIM', `animateTrickToWinner: card ${index} from (${startX.toFixed(0)}, ${startY.toFixed(0)})`);

        // Set fixed position at current location
        wrapper.style.position = 'fixed';
        wrapper.style.left = `${startX}px`;
        wrapper.style.top = `${startY}px`;
        wrapper.style.width = `${rect.width}px`;
        wrapper.style.zIndex = '1000';
        wrapper.classList.add('animating');

        console.log(`[ANIM] Card ${index}: set fixed position, adding animation class`);

        // Force reflow
        wrapper.offsetHeight;

        // Calculate translation to target
        const deltaX = targetX - (startX + rect.width / 2);
        const deltaY = targetY - (startY + rect.height / 2);

        console.log(`[ANIM] Card ${index}: deltaX=${deltaX.toFixed(0)}, deltaY=${deltaY.toFixed(0)}`);

        // Start animation
        wrapper.style.transform = `translate(${deltaX}px, ${deltaY}px) scale(0.3)`;
        wrapper.style.opacity = '0';
    });
    console.log('[ANIM] All cards animation started');

    // Wait for animation to complete (500ms + stagger delays + buffer)
    debug('ANIM', 'animateTrickToWinner: waiting 650ms for animation');
    await new Promise(resolve => setTimeout(resolve, 650));
    debug('ANIM', 'animateTrickToWinner: animation complete');
}

async function nextRound() {
    debug('GAME', 'nextRound: starting next round');

    try {
        const response = await fetch('/api/game/next-round', { method: 'POST' });

        if (!response.ok) {
            debugError('API', `nextRound: HTTP error ${response.status}`);
            showMessage(`Server error: ${response.status}`, 'error');
            return;
        }

        const data = await response.json();
        debug('API', 'nextRound: response', { success: data.success });

        if (data.success) {
            if (!data.state) {
                debugError('GAME', 'nextRound: success but no state');
                return;
            }
            gameState = data.state;
            selectedCards = [];
            debug('GAME', 'nextRound: new round started', {
                phase: gameState.current_round?.phase,
                roundNumber: gameState.round_number
            });
            renderGame();
            showMessage(t('newRoundStarted'), 'success');
        } else {
            debugError('GAME', 'nextRound: failed', data.error);
            showMessage(data.error, 'error');
        }
    } catch (error) {
        debugError('API', 'nextRound: exception', error);
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

    const auction = gameState.current_round?.auction;

    // Determine minimum level from winning bid
    // If in_hand won, contract always starts from 2
    // If regular game bid won, contract starts from that bid's value
    let minLevel = 2;
    if (auction?.highest_in_hand_bid) {
        // In-hand bid won - contract can be any level from 2
        minLevel = 2;
    } else if (auction?.highest_game_bid) {
        // Regular game bid won - contract starts from bid value
        const bidValue = auction.highest_game_bid.effective_value || auction.highest_game_bid.value || 2;
        minLevel = Math.max(2, Math.min(bidValue, 7));
    }

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
    debug('RENDER', 'renderGame: starting render');

    if (!gameState) {
        debugWarn('RENDER', 'renderGame: no game state, skipping render');
        return;
    }

    const round = gameState.current_round;
    const phase = round?.phase || 'waiting';
    const auctionPhase = gameState.auction_phase;

    debug('RENDER', 'renderGame: phase info', {
        phase,
        auctionPhase,
        currentPlayerId: gameState.current_player_id,
        currentBidderId: round?.auction?.current_bidder_id
    });

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

    if (!elements.phaseIndicator) {
        debugError('RENDER', 'renderGame: phaseIndicator element not found');
    } else {
        elements.phaseIndicator.textContent = phaseText;
    }

    // Render players
    try {
        renderPlayers();
    } catch (e) {
        debugError('RENDER', 'renderGame: renderPlayers failed', e);
    }

    // Render center area
    try {
        renderTalon();
    } catch (e) {
        debugError('RENDER', 'renderGame: renderTalon failed', e);
    }

    try {
        renderCurrentTrick();
    } catch (e) {
        debugError('RENDER', 'renderGame: renderCurrentTrick failed', e);
    }

    try {
        renderLastTrick();
    } catch (e) {
        debugError('RENDER', 'renderGame: renderLastTrick failed', e);
    }

    try {
        renderContractInfo();
    } catch (e) {
        debugError('RENDER', 'renderGame: renderContractInfo failed', e);
    }

    try {
        renderBiddingHistory();
    } catch (e) {
        debugError('RENDER', 'renderGame: renderBiddingHistory failed', e);
    }

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

    debug('RENDER', 'renderGame: complete');
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
    if (!lastTrickEl) return;

    const cardsContainer = lastTrickEl.querySelector('.last-trick-cards');
    cardsContainer.innerHTML = '';

    const round = gameState.current_round;
    if (!round || !round.tricks || round.tricks.length < 2) {
        if (round?.tricks?.length === 1 && round.tricks[0].cards?.length === 3) {
            // First trick is complete, show it
        } else {
            lastTrickEl.classList.remove('visible');
            return;
        }
    }

    // Find the last completed trick
    let lastCompletedTrick = null;
    const currentTrick = round.tricks[round.tricks.length - 1];
    if (currentTrick?.cards?.length === 3) {
        lastCompletedTrick = currentTrick;
    } else if (round.tricks.length >= 2) {
        lastCompletedTrick = round.tricks[round.tricks.length - 2];
    } else if (round.tricks.length === 1 && round.tricks[0].cards?.length === 3) {
        lastCompletedTrick = round.tricks[0];
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
            elements.playControls.classList.remove('hidden');
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
