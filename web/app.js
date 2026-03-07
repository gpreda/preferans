// Preferans Terminal UI

const output = document.getElementById('output');
const commandBar = document.getElementById('command-bar');
const gameStateEl = document.getElementById('game-state');
const handEls = {1: document.getElementById('hand-p1'), 2: document.getElementById('hand-p2'), 3: document.getElementById('hand-p3')};
const talonLine = document.getElementById('talon-line');
const prevTrickLine = document.getElementById('prev-trick-line');
const remainingLine = document.getElementById('remaining-line');
const gameInfo = document.getElementById('game-info');

const SUIT = {spades: '\u2660', diamonds: '\u2666', clubs: '\u2663', hearts: '\u2665'};
const SUIT_ORDER = ['spades', 'diamonds', 'clubs', 'hearts'];

const _SUIT_SORT = {spades: 4, diamonds: 3, clubs: 2, hearts: 1};
const _RANK_SORT = {'7': 8, '8': 7, '9': 6, '10': 5, 'J': 4, 'Q': 3, 'K': 2, 'A': 1};
const RANKS = ['7', '8', '9', '10', 'J', 'Q', 'K', 'A'];
const FULL_DECK = [];
for (const s of SUIT_ORDER) for (const r of RANKS) FULL_DECK.push(r + '_' + s);

function _sort_cards(cardIds) {
    return cardIds.slice().sort((a, b) => {
        const [ra, sa] = a.split('_');
        const [rb, sb] = b.split('_');
        const sd = -(_SUIT_SORT[sa] || 0) + (_SUIT_SORT[sb] || 0);
        return sd !== 0 ? sd : -(_RANK_SORT[ra] || 0) + (_RANK_SORT[rb] || 0);
    });
}

let state = null;
let exchangeSelected = [];
let aiTimer = null;
let handProbs = {};  // {playerPosition: {w2, w1, wh, f, b, s}}

// ── helpers ─────────────────────────────────────────────────────────

function playerName(p) {
    if (!state || !state.players) return 'P' + p;
    let name = state.players[String(p)] || 'P' + p;
    const tags = [];
    if (state.dealer === p) tags.push('D');
    if (state.phase === 'playing' && state.trick_lead === p) tags.push('L');
    if (tags.length) name += ' (' + tags.join(',') + ')';
    return name;
}

function playerVerb(p) {
    // "You have" vs "Alice has"
    return (state && state.players && state.players[String(p)] === 'You') ? 'have' : 'has';
}

function isAI(p) {
    return state && state.human && p !== state.human;
}

// ── logging ─────────────────────────────────────────────────────────

function ts() {
    return new Date().toISOString().substr(11, 12);
}

function log(text, cls = 'info', html = false) {
    const line = document.createElement('div');
    line.className = 'line ' + cls;
    if (html) {
        line.innerHTML = ts() + '  ' + text;
    } else {
        line.textContent = ts() + '  ' + text;
    }
    output.prepend(line);
}

function formatCardsHtml(cards, discardedSet, talonSet) {
    // Group cards by suit, preserving card IDs for discard lookup
    const bySuit = {};
    for (const id of cards) {
        const [rank, suit] = id.split('_');
        if (!bySuit[suit]) bySuit[suit] = [];
        bySuit[suit].push({ rank, suit, id });
    }
    const parts = [];
    for (const suit of SUIT_ORDER) {
        if (!bySuit[suit]) continue;
        const sym = SUIT[suit];
        const color = (suit === 'diamonds' || suit === 'hearts') ? '#f55' : '#fff';
        const cols = bySuit[suit].map(c => {
            const isDiscarded = discardedSet && discardedSet.has(c.id);
            const isTalon = talonSet && talonSet.has(c.id);
            let cls = '';
            if (isDiscarded) cls = ' card-discarded';
            else if (isTalon) cls = ' card-talon';
            return '<span class="card-col' + cls + '" style="color:' + color + '">'
                + '<span class="card-rank">' + c.rank + '</span>'
                + '<span class="card-suit">' + sym + '</span></span>';
        }).join('');
        parts.push('<span class="card-suit-group">' + cols + '</span>');
    }
    return parts.join('<span class="suit-gap-2row"></span>');
}

function cardHtml(cardId) {
    const [rank, suit] = cardId.split('_');
    const sym = SUIT[suit];
    const color = (suit === 'diamonds' || suit === 'hearts') ? '#f55' : '#fff';
    return '<span class="card-col" style="color:' + color + '">'
        + '<span class="card-rank">' + rank + '</span>'
        + '<span class="card-suit">' + sym + '</span></span>';
}

function sep() {
    const line = document.createElement('div');
    line.className = 'line separator';
    line.textContent = '\u2500'.repeat(72);
    output.prepend(line);
}

function cardName(id) {
    const [rank, suit] = id.split('_');
    return rank + (SUIT[suit] || suit);
}

function _logDiscardScores(cardIds, discardScores) {
    // Show discard scores sorted by score descending, for suit and betl
    for (const [label, scMap] of [['SUIT/SANS', discardScores.suit], ['BETL', discardScores.betl]]) {
        if (!scMap) continue;
        const scored = cardIds.map(id => {
            const [rank, suit] = id.split('_');
            return {rank, suit, id, score: scMap[id] || 0};
        }).sort((a, b) => b.score - a.score);
        const items = scored.map(c => {
            const sym = SUIT[c.suit];
            const color = (c.suit === 'diamonds' || c.suit === 'hearts') ? '#f55' : '#fff';
            const s = Math.round(c.score * 10) / 10;
            return '<span style="color:' + color + '">' + c.rank + sym + '</span>=' + s;
        }).join(' ');
        log(label + ': ' + items, 'gray', true);
    }
}

function contractStr(c) {
    if (!c) return 'none';
    if (c.type === 'suit') {
        const sym = SUIT[c.trump] || c.trump;
        return sym + ' ' + c.level;
    }
    return c.type;
}

function appendLastAction(el, action) {
    if (!action || el.innerHTML === '') return;
    const infoRow = el.querySelector('.player-info-row');
    const target = infoRow || el;
    const span = document.createElement('span');
    span.className = 'last-action';
    if (action.startsWith('plays ')) {
        const cardId = action.substring(6);
        span.innerHTML = cardHtml(cardId);
    } else {
        span.textContent = action;
    }
    target.appendChild(span);
}

// ── command bar ─────────────────────────────────────────────────────

function renderCommandBar(buttons) {
    commandBar.innerHTML = '';
    if (!buttons || !buttons.length) return;
    for (const b of buttons) {
        const btn = document.createElement('button');
        btn.textContent = b.label;
        if (b.active) btn.classList.add('active');
        btn.onclick = () => b.action();
        commandBar.appendChild(btn);
    }
}

// ── game state panel ────────────────────────────────────────────────

function renderGameState() {
    for (const p of [1, 2, 3]) { handEls[p].innerHTML = ''; handEls[p].className = 'player-block'; }
    talonLine.innerHTML = '';
    prevTrickLine.innerHTML = '';
    remainingLine.innerHTML = '';
    remainingLine.className = 'hand-line dim';
    gameInfo.innerHTML = '';

    if (!state) return;

    const human = state.human;
    const phase = state.phase;
    const lastActions = state.last_actions || {};

    // Render each player in position order (1, 2, 3) top-down
    // Skip hand rendering during scoring — renderInitialHands handles it
    for (const p of [1, 2, 3]) {
        const el = handEls[p];

        if (phase === 'scoring') continue;

        // Build display name with tricks count during playing
        const tpp = state.tricks_per_player || {};
        const tricks = tpp[String(p)] || 0;
        const pname = phase === 'playing' ? playerName(p) + ' [' + tricks + ']' : playerName(p);

        if (p === human) {
            // Human hand (use exchange_cards during discard phase to show all 12)
            const humanHand = (phase === 'exchange_cards' && Array.isArray(state.exchange_cards) && state.exchange_cards.length > 0)
                ? state.exchange_cards
                : state.hands[String(human)];
            if (Array.isArray(humanHand) && humanHand.length > 0) {
                const isHumanMove = state.player_on_move === human;
                if (isHumanMove && phase === 'playing') {
                    renderHandLineClickable(el, pname, humanHand, {
                        legalCards: state.commands || [],
                        onCardClick: (cardId) => playCard(human, cardId),
                    }, p);
                } else if (phase === 'exchange_cards' && state.declarer === human) {
                    renderHandLineClickable(el, pname, humanHand, {
                        selectedCards: exchangeSelected,
                        onCardClick: (cardId) => toggleExchange(cardId),
                        discardScores: state.discard_scores,
                    }, p);
                } else {
                    renderHandLine(el, pname, humanHand.length, humanHand, false, p);
                }
            }
        } else {
            // Opponent hand
            const hand = state.hands[String(p)];
            if (state.debug) {
                if (Array.isArray(hand) && hand.length > 0) {
                    renderHandLine(el, pname, hand.length, hand, true, p);
                }
            } else {
                if (hand != null) {
                    const n = Array.isArray(hand) ? hand.length : hand;
                    renderHandLineHidden(el, pname, n, p);
                }
            }
        }
        // During playing: show play order in current trick instead of last action
        if (phase === 'playing') {
            const trickCards = state.trick_cards || [];
            const orderIdx = trickCards.findIndex(tc => tc.player === p);
            if (orderIdx >= 0) {
                const infoRow = el.querySelector('.player-info-row');
                const target = infoRow || el;
                const span = document.createElement('span');
                span.className = 'last-action';
                span.textContent = ' (' + (orderIdx + 1) + ')';
                target.appendChild(span);
                if (orderIdx === 0) el.classList.add('move-first');
                if (orderIdx === 1) el.classList.add('move-second');
            }
        } else {
            appendLastAction(el, lastActions[String(p)]);
        }

        // Role highlights
        if (state.declarer && state.declarer === p) {
            el.classList.add('declarer');
        }
        if (state.followers && state.followers.includes(p)) {
            el.classList.add('follower');
        }
    }

    // Talon line: talon cards before playing, current trick + prev trick during playing
    if (phase === 'playing') {
        const trickNum = state.tricks_played + 1;
        const inTrick = state.trick_cards || [];
        if (inTrick.length > 0) {
            talonLine.appendChild(nameLabel('Trick ' + trickNum));
            for (const tc of inTrick) {
                const cardSpan = document.createElement('span');
                cardSpan.className = 'cards';
                cardSpan.innerHTML = cardHtml(tc.card);
                talonLine.appendChild(cardSpan);
            }
        } else {
            talonLine.appendChild(nameLabel('Trick ' + trickNum));
            talonLine.appendChild(document.createTextNode('\u2014'));
        }
        // Previous trick (on separate line below)
        if (state.last_trick && state.last_trick.length > 0) {
            const prevNum = state.tricks_played;
            prevTrickLine.appendChild(nameLabel('Prev ' + prevNum));
            for (const tc of state.last_trick) {
                const cardSpan = document.createElement('span');
                cardSpan.className = 'cards dim-cards';
                cardSpan.innerHTML = cardHtml(tc.card);
                prevTrickLine.appendChild(cardSpan);
            }
            if (state.last_trick_winner) {
                const winSpan = document.createElement('span');
                winSpan.className = 'last-action';
                winSpan.textContent = ' \u2192 ' + playerName(state.last_trick_winner);
                prevTrickLine.appendChild(winSpan);
            }
        }
    } else if (phase === 'exchange_cards' || phase === 'exchanging' || phase === 'whisting') {
        // Talon cards are public after auction
        if (state.revealed_talon && state.revealed_talon.length > 0) {
            talonLine.appendChild(nameLabel('Talon'));
            const span = document.createElement('span');
            span.className = 'cards';
            span.innerHTML = formatCardsHtml(state.revealed_talon);
            talonLine.appendChild(span);
        }
        // Discarded cards visible only in debug mode
        if (state.debug && state.discarded && state.discarded.length > 0) {
            talonLine.appendChild(document.createTextNode('   Discarded: '));
            const span = document.createElement('span');
            span.className = 'cards';
            span.innerHTML = formatCardsHtml(state.discarded);
            talonLine.appendChild(span);
        }
    } else if (phase === 'auction') {
        const talon = state.talon;
        if (state.debug && Array.isArray(talon)) {
            talonLine.appendChild(nameLabel('Talon'));
            const span = document.createElement('span');
            span.className = 'cards';
            span.innerHTML = formatCardsHtml(talon);
            talonLine.appendChild(span);
        } else if (talon != null) {
            talonLine.appendChild(nameLabel('Talon'));
            talonLine.appendChild(document.createTextNode('\uD83C\uDCA0 \uD83C\uDCA0'));
        }
    }
    // scoring / no game: talon line stays empty (hidden via CSS)

    // Remaining cards line (playing phase, human player)
    if (phase === 'playing' && human) {
        const known = new Set();
        // Human's own hand
        const myHand = state.hands[String(human)];
        if (Array.isArray(myHand)) myHand.forEach(c => known.add(c));
        // Revealed talon
        if (Array.isArray(state.revealed_talon)) state.revealed_talon.forEach(c => known.add(c));
        // Discarded cards
        if (Array.isArray(state.discarded)) state.discarded.forEach(c => known.add(c));
        // All played cards
        if (Array.isArray(state.played_cards)) state.played_cards.forEach(c => known.add(c));
        // Cards currently on the table
        const inTrick = state.trick_cards || [];
        inTrick.forEach(tc => known.add(tc.card));

        const remaining = FULL_DECK.filter(c => !known.has(c));
        if (remaining.length > 0) {
            const sorted = _sort_cards(remaining);
            remainingLine.className = 'player-block dim';
            const labelRow = document.createElement('div');
            labelRow.className = 'player-info-row';
            labelRow.appendChild(nameLabel('Remaining'));
            remainingLine.appendChild(labelRow);
            const row = document.createElement('div');
            row.className = 'player-cards-row';
            const span = document.createElement('span');
            span.className = 'cards';
            span.innerHTML = formatCardsHtml(sorted);
            row.appendChild(span);
            remainingLine.appendChild(row);
        }
    }

    // Game info line
    if (state.contract) {
        let text = contractStr(state.contract);
        text += '  \u2502  declarer: ' + playerName(state.declarer);
        const wa = state.whist_actions || {};
        for (const pid of [1, 2, 3]) {
            if (pid === state.declarer) continue;
            const action = wa[String(pid)];
            if (action) text += '  \u2502  ' + playerName(pid) + ': ' + action;
        }
        const tpp = state.tricks_per_player || {};
        text += '  \u2502  tricks:';
        for (const pid of [1, 2, 3]) {
            text += ' ' + playerName(pid) + ':' + (tpp[String(pid)] || 0);
        }
        gameInfo.textContent = text;
    }
}

const NAME_WIDTH = 20;

function nameLabel(name) {
    const span = document.createElement('span');
    span.className = 'player-name';
    const truncated = name.length > NAME_WIDTH ? name.substring(0, NAME_WIDTH - 1) + '\u2026' : name;
    span.textContent = truncated.padEnd(NAME_WIDTH) + ' ';
    return span;
}

function _makePlayerRows(el, name, dim, playerPos) {
    el.className = 'player-block' + (dim ? ' dim' : '');
    el.innerHTML = '';
    const infoRow = document.createElement('div');
    infoRow.className = 'player-info-row';
    infoRow.appendChild(nameLabel(name));
    // Hand probabilities on the right
    if (playerPos && handProbs[playerPos]) {
        const hp = handProbs[playerPos];
        const pct = v => (v * 100).toFixed(0);
        const probSpan = document.createElement('span');
        probSpan.className = 'hand-probs';
        probSpan.textContent = 'w2=' + pct(hp.w2) + ' w1=' + pct(hp.w1) + ' wh=' + pct(hp.wh)
            + ' f=' + pct(hp.f) + ' b=' + pct(hp.b) + ' s=' + pct(hp.s);
        infoRow.appendChild(probSpan);
    }
    el.appendChild(infoRow);
    const cardsRow = document.createElement('div');
    cardsRow.className = 'player-cards-row';
    el.appendChild(cardsRow);
    return { infoRow, cardsRow };
}

function renderHandLine(el, name, count, cards, dim, playerPos) {
    const { cardsRow } = _makePlayerRows(el, name, dim, playerPos);
    const span = document.createElement('span');
    span.className = 'cards';
    span.innerHTML = formatCardsHtml(cards);
    cardsRow.appendChild(span);
}

function renderHandLineHidden(el, name, count, playerPos) {
    const { cardsRow } = _makePlayerRows(el, name, true, playerPos);
    cardsRow.appendChild(document.createTextNode(count + ' cards (hidden)'));
}

function renderHandLineClickable(el, name, cards, options, playerPos) {
    // options: { legalCards?, onCardClick, selectedCards?, discardScores? }

    // Build underline set from discard scores (suit scores used by default)
    let ulRed = new Set(), ulYellow = new Set();
    if (options.discardScores) {
        const scMap = options.discardScores.suit || {};
        const sorted = cards.slice().sort((a, b) => (scMap[b] || 0) - (scMap[a] || 0));
        if (sorted.length >= 1) ulRed.add(sorted[0]);
        if (sorted.length >= 2) ulRed.add(sorted[1]);
        for (let k = 2; k < sorted.length; k++) {
            if ((scMap[sorted[k]] || 0) > 30) ulYellow.add(sorted[k]);
        }
    }

    const bySuit = {};
    for (const id of cards) {
        const [rank, suit] = id.split('_');
        if (!bySuit[suit]) bySuit[suit] = [];
        bySuit[suit].push({ id, rank, suit });
    }

    const { cardsRow } = _makePlayerRows(el, name, false, playerPos);
    let first = true;
    for (const suit of SUIT_ORDER) {
        if (!bySuit[suit]) continue;
        if (!first) {
            const spacer = document.createElement('span');
            spacer.className = 'suit-gap';
            cardsRow.appendChild(spacer);
        }
        first = false;

        for (const card of bySuit[suit]) {
            const btn = document.createElement('button');
            btn.className = 'card-btn';
            const sym = SUIT[card.suit];
            const color = (card.suit === 'diamonds' || card.suit === 'hearts') ? '#f55' : '#fff';
            btn.innerHTML = '<span class="card-rank">' + card.rank + '</span><span class="card-suit">' + sym + '</span>';
            btn.style.color = color;

            if (ulRed.has(card.id)) {
                btn.style.textDecoration = 'underline';
                btn.style.textDecorationColor = 'red';
                btn.style.textUnderlineOffset = '4px';
            } else if (ulYellow.has(card.id)) {
                btn.style.textDecoration = 'underline';
                btn.style.textDecorationColor = 'yellow';
                btn.style.textUnderlineOffset = '4px';
            }

            const isLegal = !options.legalCards || options.legalCards.includes(card.id);
            const isSelected = options.selectedCards && options.selectedCards.includes(card.id);

            if (isSelected) btn.classList.add('selected');
            if (!isLegal) {
                btn.classList.add('disabled');
                btn.disabled = true;
            }

            if (isLegal && options.onCardClick) {
                const cardId = card.id;
                btn.onclick = () => options.onCardClick(cardId);
            }

            cardsRow.appendChild(btn);
        }
    }
}

// ── API ─────────────────────────────────────────────────────────────

async function api(path, body) {
    const opts = body !== undefined
        ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}
        : {};
    const r = await fetch(path, opts);
    const ct = r.headers.get('content-type') || '';
    if (!ct.includes('application/json')) {
        const text = await r.text();
        throw new Error('Server error ' + r.status + ': ' + text.substring(0, 200));
    }
    const data = await r.json();
    if (!r.ok) {
        log('<< ' + r.status + ' ' + JSON.stringify(data), 'red');
        throw new Error(data.error || r.statusText);
    }
    return data;
}

// ── phase transition logging ────────────────────────────────────────

function logTransition(prevPhase, newState) {
    const phase = newState.phase;

    if (phase === 'exchange_cards' && prevPhase === 'auction') {
        log('auction winner: ' + playerName(newState.declarer), 'green');
    }

    // AI declarer: auction → exchanging or whisting (exchange happened server-side)
    if (prevPhase === 'auction' && (phase === 'exchanging' || phase === 'whisting')) {
        log('auction winner: ' + playerName(newState.declarer), 'green');
        if (newState.discarded && newState.discarded.length > 0) {
            log(playerName(newState.declarer) + ' discarded: ' + newState.discarded.map(cardName).join(' '), 'yellow');
        }
        if (phase === 'whisting' && newState.contract) {
            log('contract: ' + contractStr(newState.contract), 'green');
        }
        sep();
    }

    if (phase === 'whisting' && (prevPhase === 'exchanging' || prevPhase === 'exchange_cards')) {
        if (newState.contract) {
            log('contract: ' + contractStr(newState.contract), 'green');
        }
        if (newState.discarded && newState.discarded.length > 0) {
            log('discarded: ' + newState.discarded.map(cardName).join(' '), 'yellow');
        }
        sep();
    }

    if (phase === 'playing' && prevPhase !== 'playing') {
        sep();
        log('PLAY  ' + contractStr(newState.contract) + '  declarer=' + playerName(newState.declarer) + '  followers=[' + newState.followers.map(playerName).join(',') + ']', 'green');
        sep();
    }

    if (phase === 'scoring' && prevPhase !== 'scoring') {
        // Will be rendered by renderScoring
    }
}

// ── AI auto-play ────────────────────────────────────────────────────

function clearAiTimer() {
    if (aiTimer) {
        clearTimeout(aiTimer);
        aiTimer = null;
    }
}

function scheduleAiMove() {
    clearAiTimer();
    const p = state.player_on_move;
    if (!p || !isAI(p)) return;
    if (state.phase === 'scoring') return;

    aiTimer = setTimeout(async () => {
        try {
            const prevPhase = state.phase;
            state = await api('/api/game/ai-move', {});
            const act = state.ai_action;

            // Log what AI did
            if (act) {
                const name = playerName(act.player);
                if (act.command) {
                    const aiCmd = (prevPhase === 'whisting' && act.command === 'Pass') ? 'Not follow' : act.command;
                    log(name + ' \u2192 ' + aiCmd, 'bright');
                } else if (act.card) {
                    log(name + ' plays ' + cardName(act.card), 'bright');
                }
            }

            // Log trick result
            if (state.play_result && state.play_result.trick_complete) {
                log('\u2192 ' + playerName(state.play_result.winner) + ' wins trick ' + state.tricks_played, 'green');
                sep();
            }

            // Log phase transitions
            logTransition(prevPhase, state);

            render();
        } catch (e) {
            log('AI ERROR: ' + e.message, 'red');
        }
    }, 800);
}

// ── game flow ───────────────────────────────────────────────────────

async function newGame(debug = false) {
    try {
        clearAiTimer();
        sep();
        log(debug ? 'NEW DEBUG GAME' : 'NEW GAME', 'green');
        state = await api('/api/game/new', {debug});

        // Log player names
        const names = state.players || {};
        log(names['1'] + ' (P1) vs ' + names['2'] + ' (P2) vs ' + names['3'] + ' (P3)', 'green');

        // Fetch hand probabilities for human player (and AI in debug mode)
        handProbs = {};
        if (state.hands) {
            for (const p of [1, 2, 3]) {
                if (p === state.human || state.debug) {
                    const cards = state.hands[String(p)];
                    if (Array.isArray(cards) && cards.length === 10) {
                        _fetchHandProbabilities(p, cards);
                    }
                }
            }
        }

        sep();
        render();
    } catch (e) {
        log('ERROR: ' + e.message, 'red');
    }
}

async function _fetchHandProbabilities(playerPos, cardIds, discardedIds) {
    try {
        let url = '/api/hand-probability?cards=' + cardIds.join(',');
        if (discardedIds && discardedIds.length > 0) {
            url += '&discarded=' + discardedIds.join(',');
        }
        const resp = await fetch(url);
        const data = await resp.json();
        if (data.error) return;
        handProbs[playerPos] = {
            w2: data.strongest_suit_win_prob_all_follow_P1,
            w1: data.strongest_suit_win_prob_single_follow,
            wh: data.in_hand_win_prob,
            f: data.strongest_suit_follow_prob,
            b: data.betl_win_prob,
            s: data.sans_win_prob,
        };
        renderGameState();
    } catch (e) { /* ignore */ }
}

function render() {
    renderCommandBar([]);
    renderGameState();
    clearAiTimer();
    exchangeSelected = [];
    const phase = state.phase;

    if (phase === 'auction') renderAuction();
    else if (phase === 'exchange_cards') renderExchangeCards();
    else if (phase === 'exchanging') renderBiddingEngine('CONTRACT');
    else if (phase === 'whisting') renderBiddingEngine('WHIST');
    else if (phase === 'playing') renderPlaying();
    else if (phase === 'scoring') renderScoring();
    else log('unknown phase: ' + phase, 'red');
}

// ── AUCTION / EXCHANGING / WHISTING (bidding engine phases) ─────────

function renderAuction() {
    renderBiddingEngine('AUCTION');
}

function renderBiddingEngine(label) {
    const p = state.player_on_move;
    const name = playerName(p);

    if (isAI(p)) {
        log(label + '  ' + name + ' thinking...', 'cyan');
        scheduleAiMove();
        return;
    }

    const displayCmd = cmd => (label === 'WHIST' && cmd === 'Pass') ? 'Not follow' : cmd;
    log(label + '  ' + name + ' to act  [' + (state.commands || []).map(displayCmd).join(', ') + ']', 'cyan');
    const buttons = (state.commands || []).map((cmd, i) => ({
        label: displayCmd(cmd),
        action: () => execBiddingEngine(i + 1, cmd, p),
    }));
    renderCommandBar(buttons);
    renderGameState();
}

async function execBiddingEngine(cmdIdx, label, player) {
    try {
        const prevPhase = state.phase;
        const displayLabel = (prevPhase === 'whisting' && label === 'Pass') ? 'Not follow' : label;
        log(playerName(player) + ' \u2192 ' + displayLabel, 'bright');
        state = await api('/api/game/execute', {command_id: cmdIdx});

        logTransition(prevPhase, state);
        render();
    } catch (e) {
        log('ERROR: ' + e.message, 'red');
    }
}

// ── EXCHANGE CARDS ──────────────────────────────────────────────────

function renderExchangeCards() {
    const p = state.declarer;
    const name = playerName(p);

    if (isAI(p)) {
        log('EXCHANGE  ' + name + ' exchanging...', 'cyan');
        scheduleAiMove();
        return;
    }

    log('EXCHANGE  ' + name + ' ' + playerVerb(p) + ' ' + state.exchange_cards.length + ' cards \u2014 click 2 to discard', 'cyan');

    // Debug: show discard scores for each card
    if (state.discard_scores) {
        _logDiscardScores(state.exchange_cards, state.discard_scores);
    }

    // Only CONFIRM in command bar; card selection is in the hand display
    renderCommandBar([{label: 'CONFIRM (' + exchangeSelected.length + '/2)', action: confirmExchange}]);
    renderGameState();
}

function toggleExchange(card) {
    const idx = exchangeSelected.indexOf(card);
    if (idx >= 0) exchangeSelected.splice(idx, 1);
    else if (exchangeSelected.length < 2) exchangeSelected.push(card);
    renderCommandBar([{label: 'CONFIRM (' + exchangeSelected.length + '/2)', action: confirmExchange}]);
    renderGameState();

    // When 2 cards selected, recompute probabilities with discard
    if (exchangeSelected.length === 2 && state.exchange_cards) {
        const discarded = new Set(exchangeSelected);
        const remaining = state.exchange_cards.filter(c => !discarded.has(c));
        _fetchHandProbabilities(state.human, remaining, exchangeSelected);
    }
}

async function confirmExchange() {
    if (exchangeSelected.length !== 2) {
        log('select exactly 2 cards', 'red');
        return;
    }
    try {
        const prevPhase = state.phase;
        state = await api('/api/game/execute', {cards: exchangeSelected});

        logTransition(prevPhase, state);
        render();
    } catch (e) {
        log('ERROR: ' + e.message, 'red');
    }
}

// ── PLAYING ─────────────────────────────────────────────────────────

function renderPlaying() {
    const p = state.player_on_move;
    if (!p) return;

    const trickNum = state.tricks_played + 1;
    const name = playerName(p);

    if (isAI(p)) {
        log('TRICK ' + trickNum + '  ' + name + ' thinking...', 'cyan');
        scheduleAiMove();
        return;
    }

    // Human turn — cards are clickable in the hand display
    renderCommandBar([]);
    renderGameState();
}

async function playCard(player, card) {
    try {
        log(playerName(player) + ' plays ' + cardName(card), 'bright');
        state = await api('/api/game/execute', {player, card});

        const pr = state.play_result;
        if (pr && pr.trick_complete) {
            log('\u2192 ' + playerName(pr.winner) + ' wins trick ' + state.tricks_played, 'green');
            sep();
        }
        render();
    } catch (e) {
        log('ERROR: ' + e.message, 'red');
    }
}

// ── SCORING ─────────────────────────────────────────────────────────

function renderScoring() {
    renderCommandBar([]);

    // Show initial hands in the hand lines during scoring
    renderInitialHands();

    sep();
    if (state.all_pass) {
        log('ALL PASSED \u2014 redeal', 'yellow');
    } else if (state.no_followers) {
        log('NO FOLLOWERS \u2014 ' + playerName(state.declarer) + ' wins with ' + contractStr(state.contract), 'yellow');
        renderScoreTable();
    } else {
        const s = state.scoring;
        const won = s && s.declarer_won;
        const result = won ? 'WON' : 'LOST';
        const resultCls = won ? 'green' : 'red';
        log('GAME OVER  ' + playerName(state.declarer) + ' ' + result + '  contract: ' + contractStr(state.contract), resultCls);

        // Needed tricks
        const needed = state.contract && state.contract.type === 'betl' ? 0 : 6;
        const dt = s ? s.declarer_tricks : (state.tricks_won || {})[String(state.declarer)] || 0;
        log('  declarer took ' + dt + ' tricks (needed ' + needed + ')', 'info');

        renderScoreTable();
    }
    sep();
    log('click New Game to play again', 'dim');
}

function renderInitialHands() {
    const ih = state.initial_hands;
    const it = state.initial_talon;
    if (!ih) return;

    const decl = state.declarer;
    const discarded = state.discarded || [];
    const discardedSet = new Set(discarded);
    // Did declarer go through exchange? (has talon cards and discards)
    const hadExchange = decl && Array.isArray(it) && it.length > 0 && discarded.length > 0;

    for (const p of [1, 2, 3]) {
        const el = handEls[p];
        let cards = ih[String(p)];
        if (!Array.isArray(cards) || cards.length === 0) continue;

        if (hadExchange && p === decl) {
            // Merge talon into declarer's hand, show discards with strikethrough
            const merged = _sort_cards(cards.concat(it));
            const talonSet = new Set(it);
            renderHandLineWithDiscards(el, playerName(p), merged, discardedSet, talonSet, p);
        } else {
            renderHandLine(el, playerName(p), cards.length, cards, false, p);
        }
        if (decl && decl === p) el.classList.add('declarer');
    }

    // Only show talon separately if there was no exchange
    if (!hadExchange && Array.isArray(it) && it.length > 0) {
        talonLine.appendChild(nameLabel('Talon'));
        const span = document.createElement('span');
        span.className = 'cards';
        span.innerHTML = formatCardsHtml(it);
        talonLine.appendChild(span);
    }
}

function renderHandLineWithDiscards(el, name, cards, discardedSet, talonSet, playerPos) {
    const { cardsRow } = _makePlayerRows(el, name, false, playerPos);
    const span = document.createElement('span');
    span.className = 'cards';
    span.innerHTML = formatCardsHtml(cards, discardedSet, talonSet);
    cardsRow.appendChild(span);
}

function renderScoreTable() {
    const s = state.scoring;
    if (!s || !s.players) return;

    sep();
    log('SCORES', 'cyan');
    for (const p of [1, 2, 3]) {
        const pd = s.players[String(p)];
        if (!pd) continue;
        const name = playerName(p);

        // Role
        let role;
        if (p === state.declarer) {
            role = 'declarer';
        } else if (state.followers && state.followers.includes(p)) {
            role = pd.role || 'follower';
        } else {
            role = 'passed';
        }

        const tricks = pd.tricks || 0;
        const score = pd.score != null ? pd.score : 0;
        const sign = score >= 0 ? '+' : '';
        const cls = score > 0 ? 'green' : score < 0 ? 'red' : 'info';

        log('  ' + name + ' (' + role + ')  tricks: ' + tricks + '  score: ' + sign + score, cls);
    }
}

// ── Agent Panel (debug mode only) ───────────────────────────────────

const agentPanel  = document.getElementById('agent-panel');
const agentHeader = document.getElementById('agent-header');
const agentOutput = document.getElementById('agent-output');
const agentInput  = document.getElementById('agent-input');
const agentStatus = document.getElementById('agent-status');
const agentToggle = document.getElementById('agent-toggle');
const agentLoadBtn = document.getElementById('agent-load-btn');
let agentPollTimer  = null;
let agentPolling = false;

// Toggle collapsed/expanded
if (agentHeader) agentHeader.addEventListener('click', (e) => {
    if (e.target === agentInput || e.target === agentLoadBtn) return;
    agentPanel.classList.toggle('collapsed');
});

function agentAppend(text, cls) {
    const div = document.createElement('div');
    div.className = 'agent-msg ' + cls;
    if (cls === 'user') {
        div.textContent = '> ' + text;
    } else {
        div.textContent = text;
    }
    agentOutput.appendChild(div);
    agentOutput.scrollTop = agentOutput.scrollHeight;
}

function agentSetStatus(text) {
    agentStatus.textContent = text;
    agentStatus.className = text ? 'active' : '';
}

function clearAgentPoll() {
    if (agentPollTimer) {
        clearInterval(agentPollTimer);
        agentPollTimer = null;
    }
    agentPolling = false;
}

function startAgentPoll() {
    clearAgentPoll();
    agentPolling = true;
    agentPollTimer = setInterval(async () => {
        try {
            const r = await fetch('/api/agent/status');
            const data = await r.json();

            agentSetStatus(data.status_text || '');

            if (data.status === 'complete') {
                clearAgentPoll();
                agentAppend(data.response, 'assistant');
                agentSetStatus('');
            } else if (data.status === 'error') {
                clearAgentPoll();
                agentAppend('Error: ' + (data.error || 'Unknown error'), 'error');
                agentSetStatus('');
            }
        } catch (_) { /* network blip — keep polling */ }
    }, 1500);
}

// Load last agent response from server
async function agentLoadLast() {
    try {
        const r = await fetch('/api/agent/status');
        const data = await r.json();
        if (!data.question && !data.response) {
            agentAppend('(no previous agent response)', 'status');
            return;
        }
        agentOutput.innerHTML = '';
        if (data.question) agentAppend(data.question, 'user');
        if (data.status === 'complete' && data.response) {
            agentAppend(data.response, 'assistant');
        } else if (data.status === 'error') {
            agentAppend('Error: ' + (data.error || 'Unknown error'), 'error');
        } else if (data.status === 'running') {
            agentSetStatus(data.status_text || 'Running...');
            startAgentPoll();
        } else {
            agentAppend('(no response yet)', 'status');
        }
    } catch (err) {
        agentAppend('Error loading: ' + err.message, 'error');
    }
}

if (agentLoadBtn) agentLoadBtn.addEventListener('click', () => {
    agentPanel.classList.remove('collapsed');
    agentLoadLast();
});

if (agentInput) agentInput.addEventListener('keydown', async (e) => {
    if (e.key !== 'Enter' || e.shiftKey) return;
    e.preventDefault();
    const question = agentInput.value.trim();
    if (!question) return;
    agentInput.value = '';

    agentPanel.classList.remove('collapsed');
    agentAppend(question, 'user');
    clearAgentPoll();

    try {
        agentSetStatus('Sending to Claude...');
        await api('/api/agent/ask', { question });
        agentSetStatus('Claude is thinking...');
        startAgentPoll();
    } catch (err) {
        agentAppend('Error: ' + err.message, 'error');
        agentSetStatus('');
    }
});

// ── init ────────────────────────────────────────────────────────────

log('ready', 'green');
document.getElementById('new-game-btn').addEventListener('click', () => newGame(IS_DEBUG));
