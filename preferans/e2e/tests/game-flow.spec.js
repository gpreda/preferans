// @ts-check
const { test, expect } = require('@playwright/test');
const { runDSL, DSLRunner, completeExchangeWithDragDrop } = require('../lib/dsl-runner');

/**
 * Helper to setup game to exchange phase
 */
async function setupToExchangePhase(page) {
  await page.goto('/?e2e');
  await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });
  await page.click('#new-game-btn');
  await page.waitForTimeout(500);

  const runner = new DSLRunner(page);
  await runner.passAuction();
  await expect(page.locator('#exchange-controls')).toBeVisible({ timeout: 10000 });
  return runner;
}

test.describe('Game Flow', () => {

  test('new game initializes correctly', async ({ page }) => {
    await runDSL(page, `
      click:new_game
      phase:auction
      visible:bidding_controls
      hidden:exchange_controls
      hidden:contract_controls
      hidden:play_controls
    `);
  });

  test('complete auction to exchange flow', async ({ page }) => {
    await runDSL(page, `
      click:new_game
      phase:auction
      pass_auction
      phase:exchange
      visible:exchange_controls
      hidden:bidding_controls
    `);
  });

  test('talon visible during exchange', async ({ page }) => {
    await runDSL(page, `
      click:new_game
      pass_auction
      phase:exchange
      talon:2:faceup
    `);
  });

  test('exchange allows 10-12 cards in hand', async ({ page }) => {
    await setupToExchangePhase(page);

    // Check if Player 3 is declarer
    const player3IsDeclarer = await page.locator('#player3 .player-role').textContent()
      .then(t => /declarer/i.test(t || ''))
      .catch(() => false);

    if (player3IsDeclarer) {
      const handCards = page.locator('#player3 .player-cards img.card');
      await expect(handCards).toHaveCount(10);

      // Drag talon card to hand
      const talonCard = page.locator('.talon-cards img.card:first-child');
      await talonCard.dragTo(page.locator('#player3 .player-cards'));
      await page.waitForTimeout(200);

      await expect(handCards).toHaveCount(11);

      // Drag second talon card to hand
      const talonCard2 = page.locator('.talon-cards img.card:first-child');
      await talonCard2.dragTo(page.locator('#player3 .player-cards'));
      await page.waitForTimeout(200);

      await expect(handCards).toHaveCount(12);
    }
  });

  test('commit exchange reduces declarer to 10 cards', async ({ page }) => {
    await setupToExchangePhase(page);

    // Check if Player 3 is declarer
    const player3IsDeclarer = await page.locator('#player3 .player-role').textContent()
      .then(t => /declarer/i.test(t || ''))
      .catch(() => false);

    if (player3IsDeclarer) {
      await completeExchangeWithDragDrop(page);

      // Player 3 should now have 10 cards
      const player3Cards = page.locator('#player3 .player-cards img.card');
      await expect(player3Cards).toHaveCount(10, { timeout: 5000 });
    }
  });

  test('contract controls appear after commit exchange', async ({ page }) => {
    await setupToExchangePhase(page);

    const player3IsDeclarer = await page.locator('#player3 .player-role').textContent()
      .then(t => /declarer/i.test(t || ''))
      .catch(() => false);

    if (player3IsDeclarer) {
      await completeExchangeWithDragDrop(page);

      // Contract controls should now be visible
      await expect(page.locator('#contract-controls')).toBeVisible({ timeout: 5000 });
    }
  });

  test('announce contract starts playing phase', async ({ page }) => {
    await setupToExchangePhase(page);

    const player3IsDeclarer = await page.locator('#player3 .player-role').textContent()
      .then(t => /declarer/i.test(t || ''))
      .catch(() => false);

    if (player3IsDeclarer) {
      await completeExchangeWithDragDrop(page);
      await expect(page.locator('#contract-controls')).toBeVisible({ timeout: 5000 });

      // Announce contract
      await page.click('#announce-btn');

      // Should now be in playing phase
      await expect(page.locator('#play-controls')).toBeVisible({ timeout: 5000 });
    }
  });

  test('starting new game resets state', async ({ page }) => {
    await runDSL(page, `
      click:new_game
      phase:auction
      click:new_game
      phase:auction
    `);

    // All players should have 10 cards
    const player1Cards = page.locator('#player1 .player-cards img.card');
    const player2Cards = page.locator('#player2 .player-cards img.card');
    const player3Cards = page.locator('#player3 .player-cards img.card');

    await expect(player1Cards).toHaveCount(10, { timeout: 5000 });
    await expect(player2Cards).toHaveCount(10, { timeout: 5000 });
    await expect(player3Cards).toHaveCount(10, { timeout: 5000 });
  });

});
