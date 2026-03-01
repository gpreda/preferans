// Preferans Terminal UI

const output = document.getElementById('output');
const SUIT = {spades: '\u2660', diamonds: '\u2666', clubs: '\u2663', hearts: '\u2665'};

let state = null;
let exchangeSelected = [];

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
    log('>> ' + (opts.method || 'GET') + ' ' + path + (body ? ' ' + JSON.stringify(body) : ''), 'dim');
    const r = await fetch(path, opts);
    const data = await r.json();
    if (!r.ok) {
        log('<< ' + r.status + ' ' + JSON.stringify(data), 'red');
        throw new Error(data.error || r.statusText);
    }
    log('<< phase=' + data.phase + ' player=' + data.player_on_move, 'dim');
    return data;
}

// ── game flow ───────────────────────────────────────────────────────

async function newGame() {
    try {
        sep();
        log('NEW GAME', 'green');
        state = await api('/api/game/new', {});
        log('game_engine:  ' + state.ge_id, 'dim');
        log('bidding_engine: ' + state.be_id, 'dim');
        sep();
        log('talon: ' + handStr(state.talon), 'yellow');
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
        log('  P' + p + ' (' + h.length + '): ' + handStr(h), 'info');
    }
}

function render() {
    clearButtons();
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
    log(label + '  P' + p + ' to act  [' + (state.commands || []).join(', ') + ']', 'cyan');
    const buttons = (state.commands || []).map((cmd, i) => ({
        label: cmd,
        action: () => execBiddingEngine(i + 1, cmd, p),
    }));
    showButtons(buttons);
}

async function execBiddingEngine(cmdIdx, label, player) {
    try {
        log('P' + player + ' \u2192 ' + label, 'bright');
        state = await api('/api/game/execute', {command_id: cmdIdx});

        // Log phase transitions
        if (state.phase === 'exchange_cards') {
            log('auction winner: P' + state.declarer, 'green');
        } else if (state.phase === 'playing') {
            log('contract: ' + contractStr(state.contract) + '  declarer=P' + state.declarer + '  followers=[' + state.followers.join(',') + ']', 'green');
            logHands();
        }

        render();
    } catch (e) {
        log('ERROR: ' + e.message, 'red');
    }
}

// ── EXCHANGE CARDS ──────────────────────────────────────────────────

function renderExchangeCards() {
    const p = state.declarer;
    const cards = state.exchange_cards;
    log('EXCHANGE  P' + p + ' has ' + cards.length + ' cards \u2014 select 2 to discard', 'cyan');
    log('  ' + handStr(cards), 'info');

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
    // re-render buttons only (no log spam)
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
        log('P' + state.declarer + ' discards: ' + handStr(exchangeSelected), 'yellow');
        state = await api('/api/game/execute', {cards: exchangeSelected});
        const h = state.hands[String(state.declarer)];
        log('P' + state.declarer + ' hand after exchange (' + h.length + '): ' + handStr(h), 'info');
        sep();
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
    const inTrick = state.trick_cards || [];
    const hand = state.commands || [];

    if (inTrick.length === 0) {
        log('TRICK ' + trickNum + '  P' + p + ' leads  hand(' + hand.length + '): ' + handStr(hand), 'cyan');
    } else {
        const played = inTrick.map(tc => 'P' + tc.player + ':' + cardName(tc.card)).join(' ');
        log('TRICK ' + trickNum + '  ' + played + '  |  P' + p + ' hand(' + hand.length + '): ' + handStr(hand), 'dim');
    }

    const buttons = hand.map(c => ({
        label: cardName(c),
        action: () => playCard(p, c),
    }));
    showButtons(buttons);
}

async function playCard(player, card) {
    try {
        log('P' + player + ' plays ' + cardName(card), 'bright');
        state = await api('/api/game/execute', {player, card});

        const pr = state.play_result;
        if (pr && pr.trick_complete) {
            log('\u2192 P' + pr.winner + ' wins trick ' + state.tricks_played, 'green');
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
    } else {
        log('GAME OVER  contract: ' + contractStr(state.contract) + '  declarer: P' + state.declarer, 'cyan');
        const tw = state.tricks_won || {};
        for (const p of [1, 2, 3]) {
            const n = tw[String(p)] || 0;
            const role = p === state.declarer ? ' (declarer)' : state.followers.includes(p) ? ' (follower)' : ' (dropped)';
            log('  P' + p + role + ': ' + n + ' tricks', 'info');
        }
    }
    sep();
    log('click New Game to play again', 'dim');
}

// ── init ────────────────────────────────────────────────────────────

log('ready', 'green');
document.getElementById('new-game-btn').addEventListener('click', newGame);
