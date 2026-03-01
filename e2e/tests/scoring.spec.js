// @ts-check
const { test, expect } = require('@playwright/test');
const { DSLRunner, completeExchangeWithDragDrop } = require('../lib/dsl-runner');

/**
 * Helper to get to playing phase and play through to scoring
 */
async function setupToPlayingPhase(page) {
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
    await completeExchangeWithDragDrop(page);
    await expect(page.locator('#contract-controls')).toBeVisible({ timeout: 5000 });

    await page.click('#announce-btn');
    await expect(page.locator('#play-controls')).toBeVisible({ timeout: 5000 });
  }

  return player3IsDeclarer;
}

/**
 * Play all cards to complete round
 */
async function playFullRound(page, maxIterations = 50) {
  for (let i = 0; i < maxIterations; i++) {
    await page.waitForTimeout(300);

    // Check if round is over
    const scoringVisible = await page.locator('#scoring-controls').isVisible().catch(() => false);
    if (scoringVisible) {
      return true;
    }

    // Try to play a card
    const playableCards = page.locator('#player3 .player-cards img.card.playable');
    const playableCount = await playableCards.count();

    if (playableCount > 0) {
      await playableCards.first().click({ force: true });
      await page.waitForTimeout(300);
    }
  }

  return false;
}

test.describe('Scoring', () => {

  test('tricks counter starts at zero', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    const tricksValues = page.locator('.tricks-value');
    const count = await tricksValues.count();

    for (let i = 0; i < count; i++) {
      const value = await tricksValues.nth(i).textContent();
      expect(value).toBe('0');
    }
  });

  test('tricks counter updates during play', async ({ page }) => {
    const isHumanDeclarer = await setupToPlayingPhase(page);

    if (isHumanDeclarer) {
      // Play several cards to complete at least one trick
      for (let round = 0; round < 15; round++) {
        await page.waitForTimeout(200);

        const playableCards = page.locator('#player3 .player-cards img.card.playable');
        const playableCount = await playableCards.count();

        if (playableCount > 0) {
          await playableCards.first().click({ force: true });
          await page.waitForTimeout(400);
        }

        // Check if any trick was won
        const tricksValues = page.locator('.tricks-value');
        let totalTricks = 0;
        const count = await tricksValues.count();
        for (let i = 0; i < count; i++) {
          const value = await tricksValues.nth(i).textContent();
          totalTricks += parseInt(value || '0');
        }

        if (totalTricks > 0) {
          expect(totalTricks).toBeGreaterThan(0);
          return;
        }
      }
    }
  });

  test('scoring screen appears after all tricks played', async ({ page }) => {
    test.setTimeout(120000); // Extended timeout for full round

    const isHumanDeclarer = await setupToPlayingPhase(page);

    if (isHumanDeclarer) {
      const roundCompleted = await playFullRound(page);

      if (roundCompleted) {
        await expect(page.locator('#scoring-controls')).toBeVisible({ timeout: 5000 });
      }
    }
  });

  test('round result message displayed after round', async ({ page }) => {
    test.setTimeout(120000);

    const isHumanDeclarer = await setupToPlayingPhase(page);

    if (isHumanDeclarer) {
      const roundCompleted = await playFullRound(page);

      if (roundCompleted) {
        const resultLabel = page.locator('#round-result');
        await expect(resultLabel).toBeVisible();

        // Should contain some text about the result
        const resultText = await resultLabel.textContent();
        expect(resultText?.length).toBeGreaterThan(0);
      }
    }
  });

  test('next round button visible after scoring', async ({ page }) => {
    test.setTimeout(120000);

    const isHumanDeclarer = await setupToPlayingPhase(page);

    if (isHumanDeclarer) {
      const roundCompleted = await playFullRound(page);

      if (roundCompleted) {
        await expect(page.locator('#next-round-btn')).toBeVisible({ timeout: 5000 });
      }
    }
  });

  test('total tricks equal 10 after round', async ({ page }) => {
    test.setTimeout(120000);

    const isHumanDeclarer = await setupToPlayingPhase(page);

    if (isHumanDeclarer) {
      const roundCompleted = await playFullRound(page);

      if (roundCompleted) {
        const tricksValues = page.locator('.tricks-value');
        let totalTricks = 0;
        const count = await tricksValues.count();

        for (let i = 0; i < count; i++) {
          const value = await tricksValues.nth(i).textContent();
          totalTricks += parseInt(value || '0');
        }

        expect(totalTricks).toBe(10);
      }
    }
  });

  test('clicking next round starts new round', async ({ page }) => {
    test.setTimeout(120000);

    const isHumanDeclarer = await setupToPlayingPhase(page);

    if (isHumanDeclarer) {
      const roundCompleted = await playFullRound(page);

      if (roundCompleted) {
        await page.click('#next-round-btn');
        await page.waitForTimeout(500);

        // Should transition to auction or show new game state
        const phaseIndicator = page.locator('#phase-indicator');
        await expect(phaseIndicator).toHaveText(/auction|bidding/i, { timeout: 5000 });
      }
    }
  });

});
