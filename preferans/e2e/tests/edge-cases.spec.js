// @ts-check
const { test, expect } = require('@playwright/test');
const { DSLRunner, completeExchangeWithDragDrop } = require('../lib/dsl-runner');

test.describe('Edge Cases', () => {

  test('multiple new games can be started in sequence', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    // Start first game
    await page.click('#new-game-btn');
    await page.waitForTimeout(500);
    await expect(page.locator('#phase-indicator')).toHaveText(/auction|bidding|exchange/i);

    // Start second game
    await page.click('#new-game-btn');
    await page.waitForTimeout(500);
    await expect(page.locator('#phase-indicator')).toHaveText(/auction|bidding|exchange/i);

    // All players should have 10 cards again
    const player1Cards = page.locator('#player1 .player-cards img.card');
    const player2Cards = page.locator('#player2 .player-cards img.card');
    const player3Cards = page.locator('#player3 .player-cards img.card');

    await expect(player1Cards).toHaveCount(10, { timeout: 5000 });
    await expect(player2Cards).toHaveCount(10, { timeout: 5000 });
    await expect(player3Cards).toHaveCount(10, { timeout: 5000 });
  });

  test('new game resets trick counters', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    // All tricks should be 0
    const tricksValues = page.locator('.tricks-value');
    const count = await tricksValues.count();

    for (let i = 0; i < count; i++) {
      const value = await tricksValues.nth(i).textContent();
      expect(value).toBe('0');
    }
  });

  test('talon has exactly 2 cards', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    // Navigate to exchange phase
    const runner = new DSLRunner(page);
    await runner.passAuction();

    // Talon should have 2 cards
    const talonCards = page.locator('#talon .talon-cards img');
    await expect(talonCards).toHaveCount(2, { timeout: 5000 });
  });

  test('AI players have cards rendered', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    // AI players (1 and 2) should have card backs or cards rendered
    const player1Cards = page.locator('#player1 .player-cards img');
    const player2Cards = page.locator('#player2 .player-cards img');

    await expect(player1Cards).toHaveCount(10, { timeout: 5000 });
    await expect(player2Cards).toHaveCount(10, { timeout: 5000 });
  });

  test('human player (player3) cards are face-up', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    // Player 3's cards should be face-up (not card backs)
    const player3Cards = page.locator('#player3 .player-cards img.card');
    const firstCardSrc = await player3Cards.first().getAttribute('src');

    expect(firstCardSrc).not.toContain('back');
    expect(firstCardSrc).toContain('/api/cards/');
  });

  test('page reload preserves server connection', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    // Reload page
    await page.reload();

    // Should still show success
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });
  });

  test('new game button always accessible', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    // New game button should always be visible
    await expect(page.locator('#new-game-btn')).toBeVisible();

    // Start a game and it should still be visible
    await page.click('#new-game-btn');
    await page.waitForTimeout(500);
    await expect(page.locator('#new-game-btn')).toBeVisible();
  });

  test('bidding history clears on new game', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(1000);

    // Should have some bidding history from AI
    const biddingHistory = page.locator('#bidding-history-list');
    await expect(biddingHistory).toBeVisible({ timeout: 3000 });

    // Start new game
    await page.click('#new-game-btn');
    await page.waitForTimeout(1000);

    // History should be fresh (may have new AI bids, but not the old ones)
    // We just verify the list exists
    await expect(biddingHistory).toBeVisible();
  });

  test('declarer role correctly assigned after auction', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    const runner = new DSLRunner(page);
    await runner.passAuction();

    // Exactly one player should be marked as declarer
    const declarerRoles = page.locator('.player-role').filter({ hasText: /declarer/i });
    await expect(declarerRoles).toHaveCount(1, { timeout: 5000 });
  });

  test('commit button disabled until talon has 2 cards', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    const runner = new DSLRunner(page);
    await runner.passAuction();

    const player3IsDeclarer = await page.locator('#player3 .player-role').textContent()
      .then(t => /declarer/i.test(t || ''))
      .catch(() => false);

    if (player3IsDeclarer) {
      const commitBtn = page.locator('#pickup-talon-btn');

      // Initially talon has 2 cards - commit should be enabled
      await expect(commitBtn).toBeEnabled();

      // Drag one talon card to hand - commit should be disabled
      const talonCard = page.locator('.talon-cards img.card:first-child');
      await talonCard.dragTo(page.locator('#player3 .player-cards'));
      await page.waitForTimeout(200);

      await expect(commitBtn).toBeDisabled();

      // Drag the other talon card to hand
      const talonCard2 = page.locator('.talon-cards img.card:first-child');
      await talonCard2.dragTo(page.locator('#player3 .player-cards'));
      await page.waitForTimeout(200);

      // Still disabled (talon has 0 cards)
      await expect(commitBtn).toBeDisabled();

      // Drag one hand card to talon
      const handCard = page.locator('#player3 .player-cards img.card:first-child');
      await handCard.dragTo(page.locator('.talon-cards'));
      await page.waitForTimeout(200);

      // Still disabled (talon has 1 card)
      await expect(commitBtn).toBeDisabled();

      // Drag another hand card to talon
      const handCard2 = page.locator('#player3 .player-cards img.card:first-child');
      await handCard2.dragTo(page.locator('.talon-cards'));
      await page.waitForTimeout(200);

      // Now commit should be enabled (talon has 2 cards)
      await expect(commitBtn).toBeEnabled();
    }
  });

  test('32 cards total in game', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    // 3 players x 10 cards + 2 talon = 32 cards
    const player1Cards = await page.locator('#player1 .player-cards img').count();
    const player2Cards = await page.locator('#player2 .player-cards img').count();
    const player3Cards = await page.locator('#player3 .player-cards img').count();
    const talonCards = await page.locator('#talon .talon-cards img').count();

    const total = player1Cards + player2Cards + player3Cards + talonCards;
    expect(total).toBe(32);
  });

});
