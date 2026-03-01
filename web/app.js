// Preferans Terminal UI

const output = document.getElementById('output');
const SUIT = {spades: '\u2660', diamonds: '\u2666', clubs: '\u2663', hearts: '\u2665'};
const SUIT_ORDER = ['spades', 'diamonds', 'clubs', 'hearts'];

let state = null;
let exchangeSelected = [];
let aiTimer = null;

// ── helpers ─────────────────────────────────────────────────────────

function playerName(p) {
    if (!state || !state.players) return 'P' + p;
    return state.players[String(p)] || 'P' + p;
}

function isAI(p) {
    return state && state.human && p !== state.human;
}

// ── logging ─────────────────────────────────────────────────────────

function ts() {
    return new Date().toISOString().substr(11, 12);
}

function log(text, cls = 'info') {
    const line = document.createElement('div');
    line.className = 'line ' + cls;
    line.textContent = ts() + '  ' + text;
    output.prepend(line);
}

function logCards(label, cards, cls = 'info') {
    const line = document.createElement('div');
    line.className = 'line ' + cls;

    const prefix = document.createTextNode(ts() + '  ' + label);
    line.appendChild(prefix);

    const cardSpan = document.createElement('span');
    cardSpan.className = 'cards';
    cardSpan.innerHTML = formatCardsHtml(cards);
    line.appendChild(cardSpan);

    output.prepend(line);
}

function formatCardsHtml(cards) {
    // Group cards by suit
    const bySuit = {};
    for (const id of cards) {
        const [rank, suit] = id.split('_');
        if (!bySuit[suit]) bySuit[suit] = [];
        bySuit[suit].push(rank);
    }
    const parts = [];
    for (const suit of SUIT_ORDER) {
        if (!bySuit[suit]) continue;
        const sym = SUIT[suit];
        const color = (suit === 'diamonds' || suit === 'hearts') ? '#f55' : '#fff';
        const ranks = bySuit[suit].map(r => '<span style="color:' + color + '">' + r + sym + '</span>').join(' ');
        parts.push(ranks);
    }
    return parts.join('&nbsp;&nbsp;&nbsp;');
}

function cardHtml(cardId) {
    const [rank, suit] = cardId.split('_');
    const sym = SUIT[suit];
    const color = (suit === 'diamonds' || suit === 'hearts') ? '#f55' : '#fff';
    return '<span style="color:' + color + '">' + rank + sym + '</span>';
}

// Log a line with mixed text and big-font cards.
// parts: strings for normal text, {card: id} for single card, {cards: [...]} for grouped hand
function logLine(cls, ...parts) {
    const line = document.createElement('div');
    line.className = 'line ' + cls;
    line.appendChild(document.createTextNode(ts() + '  '));
    for (const part of parts) {
        if (typeof part === 'string') {
            line.appendChild(document.createTextNode(part));
        } else if (part.cards) {
            const span = document.createElement('span');
            span.className = 'cards';
            span.innerHTML = formatCardsHtml(part.cards);
            line.appendChild(span);
        } else if (part.card) {
            const span = document.createElement('span');
            span.className = 'cards';
            span.innerHTML = cardHtml(part.card);
            line.appendChild(span);
        }
    }
    output.prepend(line);
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

function handStr(cards) {
    return cards.map(cardName).join(' ');
}

function contractStr(c) {
    if (!c) return 'none';
    if (c.type === 'suit') return c.trump + ' (level ' + c.level + ')';
    return c.type;
}

// ── buttons ─────────────────────────────────────────────────────────

function clearButtons() {
    const old = document.getElementById('action-bar');
    if (old) old.remove();
}

function showButtons(buttons) {
    clearButtons();
    if (!buttons.length) return;
    const bar = document.createElement('div');
    bar.id = 'action-bar';
    bar.style.cssText = 'padding:6px 12px;background:#111;border-bottom:1px solid #333;display:flex;flex-wrap:wrap;gap:4px;flex-shrink:0';
    for (const b of buttons) {
        const btn = document.createElement('button');
        btn.textContent = b.label;
        btn.style.cssText = 'font-family:Courier New,monospace;font-size:12px;padding:2px 8px;background:#222;color:#5ff;border:1px solid #333;cursor:pointer';
        if (b.active) btn.style.borderColor = btn.style.color = '#f55';
        btn.onmouseenter = () => btn.style.background = '#333';
        btn.onmouseleave = () => btn.style.background = '#222';
        btn.onclick = () => b.action();
        bar.appendChild(btn);
    }
    const terminal = document.getElementById('terminal');
    terminal.insertBefore(bar, output);
}

// ── API ─────────────────────────────────────────────────────────────

async function api(path, body) {
    const opts = body !== undefined
        ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}
        : {};
    const r = await fetch(path, opts);
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

    if (phase === 'whisting' && (prevPhase === 'exchanging' || prevPhase === 'exchange_cards')) {
        // Contract announced, show discarded cards
        if (newState.contract) {
            log('contract: ' + contractStr(newState.contract), 'green');
        }
        if (newState.discarded && newState.discarded.length > 0) {
            logCards('discarded: ', newState.discarded, 'yellow');
        }
        sep();
    }

    if (phase === 'playing' && prevPhase !== 'playing') {
        sep();
        log('PLAY  ' + contractStr(newState.contract) + '  declarer=' + playerName(newState.declarer) + '  followers=[' + newState.followers.map(playerName).join(',') + ']', 'green');
        logHands();
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
                    log(name + ' \u2192 ' + act.command, 'bright');
                } else if (act.card) {
                    logLine('bright', name + ' plays ', {card: act.card});
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

        sep();
        // Talon: hidden
        const talon = state.talon;
        if (typeof talon === 'number') {
            log('talon: ' + talon + ' cards (hidden)', 'yellow');
        } else {
            logCards('talon: ', talon, 'yellow');
        }
        logHands();
        sep();
        render();
    } catch (e) {
        log('ERROR: ' + e.message, 'red');
    }
}

function logHands() {
    for (const p of [1, 2, 3]) {
        const h = state.hands[String(p)];
        const name = playerName(p);
        if (Array.isArray(h)) {
            logCards('  ' + name + ' (' + h.length + '): ', h, 'info');
        } else {
            log('  ' + name + ': ' + h + ' cards', 'dim');
        }
    }
}

function render() {
    clearButtons();
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

    log(label + '  ' + name + ' to act  [' + (state.commands || []).join(', ') + ']', 'cyan');
    const buttons = (state.commands || []).map((cmd, i) => ({
        label: cmd,
        action: () => execBiddingEngine(i + 1, cmd, p),
    }));
    showButtons(buttons);
}

async function execBiddingEngine(cmdIdx, label, player) {
    try {
        const prevPhase = state.phase;
        log(playerName(player) + ' \u2192 ' + label, 'bright');
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

    const cards = state.exchange_cards;
    log('EXCHANGE  ' + name + ' has ' + cards.length + ' cards \u2014 select 2 to discard', 'cyan');
    logCards('  ', cards, 'info');

    const buttons = cards.map(c => ({
        label: cardName(c),
        active: exchangeSelected.includes(c),
        action: () => toggleExchange(c),
    }));
    buttons.push({label: 'CONFIRM', action: confirmExchange});
    showButtons(buttons);
}

function toggleExchange(card) {
    const idx = exchangeSelected.indexOf(card);
    if (idx >= 0) exchangeSelected.splice(idx, 1);
    else if (exchangeSelected.length < 2) exchangeSelected.push(card);
    const cards = state.exchange_cards;
    const buttons = cards.map(c => ({
        label: cardName(c),
        active: exchangeSelected.includes(c),
        action: () => toggleExchange(c),
    }));
    buttons.push({label: 'CONFIRM', action: confirmExchange});
    showButtons(buttons);
}

async function confirmExchange() {
    if (exchangeSelected.length !== 2) {
        log('select exactly 2 cards', 'red');
        return;
    }
    try {
        const prevPhase = state.phase;
        state = await api('/api/game/execute', {cards: exchangeSelected});

        const h = state.hands[String(state.declarer)];
        if (Array.isArray(h)) {
            logCards(playerName(state.declarer) + ' hand after exchange (' + h.length + '): ', h, 'info');
        }

        logTransition(prevPhase, state);
        render();
    } catch (e) {
        log('ERROR: ' + e.message, 'red');
    }
}

// ── PLAYING ─────────────────────────────────────────────────────────

function trickParts(trickNum, inTrick) {
    const parts = ['TRICK ' + trickNum + '  '];
    for (let i = 0; i < inTrick.length; i++) {
        const tc = inTrick[i];
        parts.push(playerName(tc.player) + ': ');
        parts.push({card: tc.card});
        if (i < inTrick.length - 1) parts.push('  ');
    }
    return parts;
}

function renderPlaying() {
    const p = state.player_on_move;
    if (!p) return;

    const trickNum = state.tricks_played + 1;
    const inTrick = state.trick_cards || [];
    const name = playerName(p);

    if (isAI(p)) {
        if (inTrick.length > 0) {
            logLine('dim', ...trickParts(trickNum, inTrick), '  |  ' + name + ' thinking...');
        } else {
            log('TRICK ' + trickNum + '  ' + name + ' thinking...', 'cyan');
        }
        scheduleAiMove();
        return;
    }

    // Human turn
    const hand = state.commands || [];
    if (inTrick.length === 0) {
        log('TRICK ' + trickNum + '  ' + name + ' leads', 'cyan');
    } else {
        logLine('dim', ...trickParts(trickNum, inTrick));
    }
    logCards('  hand: ', hand, 'info');

    const buttons = hand.map(c => ({
        label: cardName(c),
        action: () => playCard(p, c),
    }));
    showButtons(buttons);
}

async function playCard(player, card) {
    try {
        logLine('bright', playerName(player) + ' plays ', {card: card});
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
    clearButtons();
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

// ── init ────────────────────────────────────────────────────────────

log('ready', 'green');
document.getElementById('new-game-btn').addEventListener('click', () => newGame(false));
document.getElementById('debug-game-btn').addEventListener('click', () => newGame(true));
