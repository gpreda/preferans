// @ts-check
const { test, expect } = require('@playwright/test');
const { runDSL, DSLRunner, completeExchangeWithDragDrop } = require('../lib/dsl-runner');

/**
 * Helper to get through auction and exchange to playing phase
 * Returns true if human (player3) is declarer
 */
async function setupToPlayingPhase(page) {
  await page.goto('/?e2e');
  await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

  await page.click('#new-game-btn');
  await page.waitForTimeout(500); // Wait for game state to load

  // Use DSL runner to pass through auction
  const runner = new DSLRunner(page);
  await runner.passAuction();

  await expect(page.locator('#exchange-controls')).toBeVisible({ timeout: 10000 });

  // Check if Player 3 is declarer
  const player3IsDeclarer = await page.locator('#player3 .player-role').textContent().then(t => /declarer/i.test(t || '')).catch(() => false);

  if (player3IsDeclarer) {
    await completeExchangeWithDragDrop(page);
    await expect(page.locator('#contract-controls')).toBeVisible({ timeout: 5000 });

    // Announce contract
    await page.click('#announce-btn');
    await expect(page.locator('#play-controls')).toBeVisible({ timeout: 5000 });
  }

  return player3IsDeclarer;
}

test.describe('Playing Phase', () => {

  test('playing phase shows play controls when human is declarer', async ({ page }) => {
    const humanIsDeclarer = await setupToPlayingPhase(page);

    if (humanIsDeclarer) {
      await expect(page.locator('#play-controls')).toBeVisible({ timeout: 5000 });
    }
  });

  test('playable cards are highlighted', async ({ page }) => {
    const humanIsDeclarer = await setupToPlayingPhase(page);

    if (humanIsDeclarer) {
      // Check that some cards are marked as playable
      const playableCards = page.locator('.player-cards img.card.playable');
      const count = await playableCards.count();
      expect(count).toBeGreaterThan(0);
    }
  });

  test('playing a card removes it from hand', async ({ page }) => {
    const humanIsDeclarer = await setupToPlayingPhase(page);

    if (humanIsDeclarer) {
      // Count cards before playing
      const player3Cards = page.locator('#player3 .player-cards img.card');
      const cardsBefore = await player3Cards.count();

      // Play a card if we have playable cards
      const playableCards = page.locator('#player3 .player-cards img.card.playable');
      const playableCount = await playableCards.count();

      if (playableCount > 0) {
        await playableCards.first().click({ force: true });

        // Wait for card to be played
        await page.waitForTimeout(500);

        // Card count should decrease
        const cardsAfter = await player3Cards.count();
        expect(cardsAfter).toBeLessThan(cardsBefore);
      }
    }
  });

  test('played cards appear in trick area', async ({ page }) => {
    const humanIsDeclarer = await setupToPlayingPhase(page);

    if (humanIsDeclarer) {
      // Play a card if playable
      const playableCards = page.locator('#player3 .player-cards img.card.playable');
      const playableCount = await playableCards.count();

      if (playableCount > 0) {
        await playableCards.first().click({ force: true });

        // Check trick area has a card
        await page.waitForTimeout(300);
        const trickCards = page.locator('#current-trick .trick-cards img.card');
        const trickCount = await trickCards.count();
        expect(trickCount).toBeGreaterThanOrEqual(1);
      }
    }
  });

  test('tricks counter starts at 0', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');

    // All players should start with 0 tricks
    const tricksValues = page.locator('.tricks-value');
    const count = await tricksValues.count();

    for (let i = 0; i < count; i++) {
      const value = await tricksValues.nth(i).textContent();
      expect(value).toBe('0');
    }
  });

});
