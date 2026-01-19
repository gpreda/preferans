// @ts-check
const { expect } = require('@playwright/test');

/**
 * DSL Runner for Preferans E2E Tests
 *
 * Parses and executes compact test definitions like:
 *   click:new_game
 *   bids:[pass,2,in_hand,betl,sans]
 *   phase:bidding
 */

class DSLRunner {
  constructor(page) {
    this.page = page;
  }

  /**
   * Parse and run a DSL test script
   * @param {string} script - Multi-line DSL script
   */
  async run(script) {
    const lines = script
      .split('\n')
      .map(line => line.trim())
      .filter(line => line && !line.startsWith('#')); // Remove empty lines and comments

    for (const line of lines) {
      await this.executeLine(line);
    }
  }

  /**
   * Execute a single DSL line
   * @param {string} line
   */
  async executeLine(line) {
    const [command, ...args] = line.split(':');
    const argStr = args.join(':'); // Rejoin in case of colons in args

    switch (command) {
      case 'click':
        await this.handleClick(argStr);
        break;
      case 'bids':
        await this.assertBids(argStr);
        break;
      case 'phase':
        await this.assertPhase(argStr);
        break;
      case 'cards':
        await this.assertCards(argStr);
        break;
      case 'talon':
        await this.assertTalon(argStr);
        break;
      case 'declarer':
        await this.assertDeclarer(argStr);
        break;
      case 'tricks':
        await this.assertTricks(argStr);
        break;
      case 'select':
        await this.handleSelect(argStr);
        break;
      case 'play':
        await this.handlePlay(argStr);
        break;
      case 'playable':
        await this.assertPlayable(argStr);
        break;
      case 'active':
        await this.assertActive(argStr);
        break;
      case 'wait':
        await this.page.waitForTimeout(parseInt(argStr));
        break;
      case 'visible':
        await this.assertVisible(argStr);
        break;
      case 'hidden':
        await this.assertHidden(argStr);
        break;
      case 'message':
        await this.assertMessage(argStr);
        break;
      case 'pass_auction':
        await this.passAuction();
        break;
      case 'complete_exchange':
        await this.completeExchange();
        break;
      default:
        throw new Error(`Unknown DSL command: ${command}`);
    }
  }

  /**
   * Handle click actions
   */
  async handleClick(target) {
    const clickTargets = {
      'new_game': '#new-game-btn',
      'commit': '#pickup-talon-btn',
      'commit_exchange': '#pickup-talon-btn',
      'pickup': '#pickup-talon-btn', // Alias for backward compatibility
      'announce': '#announce-btn',
      'next_round': '#next-round-btn',
      'pass': '.bid-btn.pass-btn',
      '2': '.bid-btn[data-bid-type="game"][data-value="2"]',
      '3': '.bid-btn[data-bid-type="game"][data-value="3"]',
      '4': '.bid-btn[data-bid-type="game"][data-value="4"]',
      '5': '.bid-btn[data-bid-type="game"][data-value="5"]',
      '6': '.bid-btn[data-bid-type="game"][data-value="6"]',
      'betl': '.bid-btn[data-bid-type="betl"]',
      'sans': '.bid-btn[data-bid-type="sans"]',
      'in_hand': '.bid-btn[data-bid-type="in_hand"]',
    };

    const selector = clickTargets[target];
    if (!selector) {
      throw new Error(`Unknown click target: ${target}`);
    }

    await this.page.locator(selector).click({ timeout: 5000 });
    await this.page.waitForTimeout(100); // Small delay for UI updates
  }

  /**
   * Assert exactly these bid options are available
   * Format: bids:[pass,2,in_hand,betl,sans]
   */
  async assertBids(argStr) {
    // Parse [pass,2,in_hand,betl,sans]
    const match = argStr.match(/\[([^\]]*)\]/);
    if (!match) {
      throw new Error(`Invalid bids format: ${argStr}`);
    }

    const expectedBids = match[1].split(',').map(b => b.trim()).filter(b => b);

    // Wait for bid buttons to be present
    await this.page.waitForTimeout(200);

    // Get all visible bid buttons
    const bidButtons = this.page.locator('#bid-buttons .bid-btn');
    const count = await bidButtons.count();

    const actualBids = [];
    for (let i = 0; i < count; i++) {
      const btn = bidButtons.nth(i);
      const bidType = await btn.getAttribute('data-bid-type');
      const bidValue = await btn.getAttribute('data-value');

      if (bidType === 'pass') {
        actualBids.push('pass');
      } else if (bidType === 'game') {
        actualBids.push(bidValue);
      } else if (bidType === 'in_hand') {
        actualBids.push('in_hand');
      } else if (bidType === 'betl') {
        actualBids.push('betl');
      } else if (bidType === 'sans') {
        actualBids.push('sans');
      }
    }

    // Sort both arrays for comparison
    const sortedExpected = [...expectedBids].sort();
    const sortedActual = [...actualBids].sort();

    expect(sortedActual, `Expected bids ${JSON.stringify(expectedBids)}, got ${JSON.stringify(actualBids)}`).toEqual(sortedExpected);
  }

  /**
   * Assert current game phase
   */
  async assertPhase(expectedPhase) {
    const phaseIndicator = this.page.locator('#phase-indicator');

    // Map phase names to what appears in the UI
    const phaseMap = {
      'bidding': /bidding|auction/i,
      'auction': /bidding|auction/i,
      'exchanging': /exchang/i,
      'exchange': /exchang/i,
      'contracting': /contract/i,
      'contract': /contract/i,
      'playing': /play/i,
      'play': /play/i,
      'scoring': /scor|result/i,
    };

    const pattern = phaseMap[expectedPhase.toLowerCase()];
    if (!pattern) {
      throw new Error(`Unknown phase: ${expectedPhase}`);
    }

    await expect(phaseIndicator).toHaveText(pattern, { timeout: 5000 });
  }

  /**
   * Assert card count for declarer
   * Format: cards:N or cards:playerN:N
   */
  async assertCards(argStr) {
    const parts = argStr.split(':');
    let selector, expectedCount;

    if (parts.length === 1) {
      // cards:N - check declarer's cards
      const declarerPlayer = this.page.locator('.player:has(.player-role:text("DECLARER"))');
      selector = declarerPlayer.locator('.player-cards img.card');
      expectedCount = parseInt(parts[0]);
    } else {
      // cards:playerN:N
      const playerId = parts[0];
      selector = this.page.locator(`#${playerId} .player-cards img.card`);
      expectedCount = parseInt(parts[1]);
    }

    await expect(selector).toHaveCount(expectedCount, { timeout: 5000 });
  }

  /**
   * Assert talon state
   * Format: talon:N:state (faceup/facedown/hidden)
   */
  async assertTalon(argStr) {
    const parts = argStr.split(':');
    const expectedCount = parseInt(parts[0]);
    const state = parts[1] || 'faceup';

    const talonCards = this.page.locator('#talon .talon-cards img');

    if (state === 'hidden' || expectedCount === 0) {
      await expect(talonCards).toHaveCount(0, { timeout: 5000 });
      return;
    }

    await expect(talonCards).toHaveCount(expectedCount, { timeout: 5000 });

    if (state === 'faceup') {
      // Check cards are face-up (src contains card id, not 'back')
      const firstCard = talonCards.first();
      const src = await firstCard.getAttribute('src');
      expect(src).not.toContain('back');
      expect(src).toContain('/api/cards/');
    } else if (state === 'facedown') {
      // Check cards are face-down (src contains 'back')
      const firstCard = talonCards.first();
      const src = await firstCard.getAttribute('src');
      expect(src).toContain('back');
    }
  }

  /**
   * Assert who is the declarer
   * Format: declarer:player1 or declarer:player2 or declarer:player3
   */
  async assertDeclarer(playerId) {
    const declarerRole = this.page.locator(`#${playerId} .player-role`);
    await expect(declarerRole).toContainText(/declarer/i, { timeout: 5000 });
  }

  /**
   * Assert trick count for a player or declarer
   * Format: tricks:N or tricks:playerN:N
   */
  async assertTricks(argStr) {
    const parts = argStr.split(':');
    let selector, expectedCount;

    if (parts.length === 1) {
      // tricks:N - check declarer's tricks
      const declarerPlayer = this.page.locator('.player:has(.player-role:text("DECLARER"))');
      selector = declarerPlayer.locator('.tricks-value');
      expectedCount = parts[0];
    } else {
      // tricks:playerN:N
      const playerId = parts[0];
      selector = this.page.locator(`#${playerId} .tricks-value`);
      expectedCount = parts[1];
    }

    await expect(selector).toHaveText(expectedCount, { timeout: 5000 });
  }

  /**
   * Select a card (for discarding)
   * Format: select:card:N
   */
  async handleSelect(argStr) {
    const parts = argStr.split(':');
    if (parts[0] !== 'card') {
      throw new Error(`Unknown select target: ${argStr}`);
    }

    const index = parseInt(parts[1]);
    const selectableCards = this.page.locator('.player-cards img.card.selectable');
    await selectableCards.nth(index).click({ force: true, timeout: 5000 });
  }

  /**
   * Play a card
   * Format: play:card:N
   */
  async handlePlay(argStr) {
    const parts = argStr.split(':');
    if (parts[0] !== 'card') {
      throw new Error(`Unknown play target: ${argStr}`);
    }

    const index = parseInt(parts[1]);
    const playableCards = this.page.locator('.player-cards img.card.playable');
    await playableCards.nth(index).click({ force: true, timeout: 5000 });
  }

  /**
   * Assert number of playable cards
   * Format: playable:N
   */
  async assertPlayable(argStr) {
    const expectedCount = parseInt(argStr);
    const playableCards = this.page.locator('.player-cards img.card.playable');
    await expect(playableCards).toHaveCount(expectedCount, { timeout: 5000 });
  }

  /**
   * Assert which player is active (has active class)
   * Format: active:player1 or active:declarer
   */
  async assertActive(target) {
    let selector;
    if (target === 'declarer') {
      selector = '.player.active:has(.player-role:text("DECLARER"))';
    } else {
      selector = `#${target}.active`;
    }

    await expect(this.page.locator(selector)).toBeVisible({ timeout: 5000 });
  }

  /**
   * Assert element is visible
   * Format: visible:selector_name
   */
  async assertVisible(target) {
    const visibleTargets = {
      'bidding_controls': '#bidding-controls',
      'exchange_controls': '#exchange-controls',
      'contract_controls': '#contract-controls',
      'play_controls': '#play-controls',
      'scoring_controls': '#scoring-controls',
      'talon': '#talon',
      'commit_btn': '#pickup-talon-btn',
      'pickup_btn': '#pickup-talon-btn', // Alias for backward compatibility
    };

    const selector = visibleTargets[target] || target;
    await expect(this.page.locator(selector)).toBeVisible({ timeout: 5000 });
  }

  /**
   * Assert element is hidden
   * Format: hidden:selector_name
   */
  async assertHidden(target) {
    const hiddenTargets = {
      'bidding_controls': '#bidding-controls',
      'exchange_controls': '#exchange-controls',
      'contract_controls': '#contract-controls',
      'play_controls': '#play-controls',
      'scoring_controls': '#scoring-controls',
      'talon': '#talon',
    };

    const selector = hiddenTargets[target] || target;
    await expect(this.page.locator(selector)).toBeHidden({ timeout: 5000 });
  }

  /**
   * Assert message area contains text
   * Format: message:text
   */
  async assertMessage(text) {
    await expect(this.page.locator('#message-area')).toContainText(text, { timeout: 5000 });
  }

  /**
   * Pass through auction until exchange phase
   * Bids Game 2 first to ensure someone becomes declarer, then passes
   */
  async passAuction() {
    let hasBid = false;

    for (let i = 0; i < 20; i++) {
      // Check if we've reached exchange phase
      const exchangeVisible = await this.page.locator('#exchange-controls:not(.hidden)').isVisible().catch(() => false);
      if (exchangeVisible) {
        return;
      }

      // Wait for bid buttons to be available
      const bidButtonsVisible = await this.page.locator('#bid-buttons').isVisible({ timeout: 1000 }).catch(() => false);
      if (!bidButtonsVisible) {
        await this.page.waitForTimeout(500);
        continue;
      }

      // If we haven't bid yet, try to make a Game 2 bid
      if (!hasBid) {
        const gameBidBtn = this.page.locator('.bid-btn[data-bid-type="game"][data-value="2"]');
        if (await gameBidBtn.isVisible({ timeout: 500 }).catch(() => false)) {
          await gameBidBtn.click();
          hasBid = true;
          await this.page.waitForTimeout(1000);
          continue;
        }
      }

      // Try to click pass if available
      const passBtn = this.page.locator('.bid-btn[data-bid-type="pass"]');
      if (await passBtn.isVisible({ timeout: 500 }).catch(() => false)) {
        await passBtn.click();
        await this.page.waitForTimeout(1000);
      } else {
        await this.page.waitForTimeout(500);
      }
    }

    // Verify we reached exchange
    await expect(this.page.locator('#exchange-controls')).toBeVisible({ timeout: 10000 });
  }

  /**
   * Complete exchange phase using drag-and-drop
   * Only works if human player (player3) is declarer
   */
  async completeExchange() {
    // Wait for exchange controls
    await expect(this.page.locator('#exchange-controls')).toBeVisible({ timeout: 5000 });

    // Check if Player 3 is declarer
    const player3IsDeclarer = await this.page.locator('#player3 .player-role').textContent().then(t => /declarer/i.test(t || '')).catch(() => false);

    if (player3IsDeclarer) {
      // Drag both talon cards to hand
      const talonCard1 = this.page.locator('.talon-cards img.card:first-child');
      const playerCards = this.page.locator('#player3 .player-cards');

      await talonCard1.dragTo(playerCards);
      await this.page.waitForTimeout(200);

      const talonCard2 = this.page.locator('.talon-cards img.card:first-child');
      await talonCard2.dragTo(playerCards);
      await this.page.waitForTimeout(200);

      // Drag first two hand cards to talon
      const talonContainer = this.page.locator('.talon-cards');
      const handCard1 = this.page.locator('#player3 .player-cards img.card:first-child');
      await handCard1.dragTo(talonContainer);
      await this.page.waitForTimeout(200);

      const handCard2 = this.page.locator('#player3 .player-cards img.card:first-child');
      await handCard2.dragTo(talonContainer);
      await this.page.waitForTimeout(200);

      // Click commit exchange
      const commitBtn = this.page.locator('#pickup-talon-btn');
      await expect(commitBtn).toBeEnabled({ timeout: 5000 });
      await commitBtn.click();

      // Wait for contract controls
      await expect(this.page.locator('#contract-controls')).toBeVisible({ timeout: 5000 });

      // Announce contract
      await this.page.click('#announce-btn');

      // Wait for playing phase
      await expect(this.page.locator('#play-controls')).toBeVisible({ timeout: 5000 });
    }
  }
}

/**
 * Helper to create a test runner with page setup
 */
async function runDSL(page, script) {
  // Wait for server connection first
  await page.goto('/');
  await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

  const runner = new DSLRunner(page);
  await runner.run(script);
}

/**
 * Helper to complete exchange using drag-and-drop
 * Works only if Player 3 (human) is the declarer
 */
async function completeExchangeWithDragDrop(page) {
  // Drag both talon cards to hand
  const talonCard1 = page.locator('.talon-cards img.card:first-child');
  const playerCards = page.locator('#player3 .player-cards');

  await talonCard1.dragTo(playerCards);
  await page.waitForTimeout(200);

  const talonCard2 = page.locator('.talon-cards img.card:first-child');
  await talonCard2.dragTo(playerCards);
  await page.waitForTimeout(200);

  // Drag first two hand cards to talon
  const talonContainer = page.locator('.talon-cards');
  const handCard1 = page.locator('#player3 .player-cards img.card:first-child');
  await handCard1.dragTo(talonContainer);
  await page.waitForTimeout(200);

  const handCard2 = page.locator('#player3 .player-cards img.card:first-child');
  await handCard2.dragTo(talonContainer);
  await page.waitForTimeout(200);

  // Click commit exchange
  const commitBtn = page.locator('#pickup-talon-btn');
  await expect(commitBtn).toBeEnabled({ timeout: 5000 });
  await commitBtn.click();
}

module.exports = { DSLRunner, runDSL, completeExchangeWithDragDrop };
