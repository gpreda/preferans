// @ts-check
const { test, expect } = require('@playwright/test');

/**
 * Helper to start a new game and complete the auction with Player 3 (human) winning
 * Bids "Game 2" then passes from other players
 */
async function startGameAndCompleteAuction(page) {
  await page.goto('/');

  // Wait for server connection
  await expect(page.locator('#message-area')).toContainText('running', { timeout: 10000 });

  // Start new game
  await page.click('#new-game-btn');

  // Wait for bidding phase
  await expect(page.locator('#bidding-controls')).toBeVisible({ timeout: 5000 });

  // Player 3 bids "Game 2" (the lowest game bid)
  // Wait for bid buttons to appear
  await expect(page.locator('#bid-buttons .bid-btn')).toHaveCount.greaterThan(0);

  // Find and click a game bid button (value 2 = Game 2)
  const gameBidBtn = page.locator('.bid-btn[data-bid-type="game"][data-value="2"]');
  if (await gameBidBtn.isVisible()) {
    await gameBidBtn.click();
  } else {
    // If specific bid not available, just pass
    await page.locator('.pass-btn').click();
  }

  // Wait for auction to complete - other players should pass
  // Keep clicking pass if we're still in auction
  for (let i = 0; i < 5; i++) {
    const passBtn = page.locator('.pass-btn');
    if (await passBtn.isVisible({ timeout: 1000 }).catch(() => false)) {
      await passBtn.click();
      await page.waitForTimeout(300);
    } else {
      break;
    }
  }

  // Wait for exchange phase
  await expect(page.locator('#exchange-controls')).toBeVisible({ timeout: 5000 });
}

test.describe('Exchange Flow', () => {

  test('talon shows face-up cards during exchange', async ({ page }) => {
    await page.goto('/');

    // Wait for server connection
    await expect(page.locator('#message-area')).toContainText('running', { timeout: 10000 });

    // Start new game
    await page.click('#new-game-btn');

    // Wait for bidding phase
    await expect(page.locator('#bidding-controls')).toBeVisible({ timeout: 5000 });

    // Bid game 2 if available, otherwise pass
    const gameBidBtn = page.locator('.bid-btn[data-bid-type="game"][data-value="2"]');
    if (await gameBidBtn.isVisible({ timeout: 1000 }).catch(() => false)) {
      await gameBidBtn.click();
    }

    // Pass through remaining auction
    for (let i = 0; i < 10; i++) {
      const passBtn = page.locator('.pass-btn');
      if (await passBtn.isVisible({ timeout: 500 }).catch(() => false)) {
        await passBtn.click();
        await page.waitForTimeout(200);
      }

      // Check if we're in exchange phase
      const exchangeVisible = await page.locator('#exchange-controls').isVisible().catch(() => false);
      if (exchangeVisible) break;
    }

    // Verify talon is visible with face-up cards
    await expect(page.locator('#talon')).toBeVisible();

    const talonCards = page.locator('#talon .talon-cards img');
    await expect(talonCards).toHaveCount(2);

    // Check that talon cards are face-up (src should contain card id, not 'back')
    const firstCard = talonCards.first();
    const cardSrc = await firstCard.getAttribute('src');
    expect(cardSrc).not.toContain('back');
    expect(cardSrc).toContain('/api/cards/');
  });

  test('contract controls hidden before exchange complete', async ({ page }) => {
    await page.goto('/');

    // Wait for server connection
    await expect(page.locator('#message-area')).toContainText('running', { timeout: 10000 });

    // Start new game
    await page.click('#new-game-btn');

    // Wait for bidding phase
    await expect(page.locator('#bidding-controls')).toBeVisible({ timeout: 5000 });

    // Bid game 2 if available
    const gameBidBtn = page.locator('.bid-btn[data-bid-type="game"][data-value="2"]');
    if (await gameBidBtn.isVisible({ timeout: 1000 }).catch(() => false)) {
      await gameBidBtn.click();
    }

    // Pass through remaining auction
    for (let i = 0; i < 10; i++) {
      const passBtn = page.locator('.pass-btn');
      if (await passBtn.isVisible({ timeout: 500 }).catch(() => false)) {
        await passBtn.click();
        await page.waitForTimeout(200);
      }

      // Check if we're in exchange phase
      const exchangeVisible = await page.locator('#exchange-controls').isVisible().catch(() => false);
      if (exchangeVisible) break;
    }

    // Verify exchange controls are visible
    await expect(page.locator('#exchange-controls')).toBeVisible();
    await expect(page.locator('#pickup-talon-btn')).toBeVisible();

    // Verify contract controls are NOT visible yet
    await expect(page.locator('#contract-controls')).toBeHidden();
  });

  test('full exchange flow completes correctly', async ({ page }) => {
    await page.goto('/');

    // Wait for server connection
    await expect(page.locator('#message-area')).toContainText('running', { timeout: 10000 });

    // Start new game
    await page.click('#new-game-btn');

    // Wait for bidding phase
    await expect(page.locator('#bidding-controls')).toBeVisible({ timeout: 5000 });

    // Bid game 2 if available
    const gameBidBtn = page.locator('.bid-btn[data-bid-type="game"][data-value="2"]');
    if (await gameBidBtn.isVisible({ timeout: 1000 }).catch(() => false)) {
      await gameBidBtn.click();
    }

    // Pass through remaining auction
    for (let i = 0; i < 10; i++) {
      const passBtn = page.locator('.pass-btn');
      if (await passBtn.isVisible({ timeout: 500 }).catch(() => false)) {
        await passBtn.click();
        await page.waitForTimeout(200);
      }

      // Check if we're in exchange phase
      const exchangeVisible = await page.locator('#exchange-controls').isVisible().catch(() => false);
      if (exchangeVisible) break;
    }

    // Verify we're in exchange phase
    await expect(page.locator('#exchange-controls')).toBeVisible();

    // Click pickup talon button
    await page.click('#pickup-talon-btn');

    // Wait for declarer to have 12 cards
    // The declarer is player 3 (human player) at bottom
    const player3Cards = page.locator('#player3 .player-cards img.card');
    await expect(player3Cards).toHaveCount(12, { timeout: 5000 });

    // Select 2 cards to discard (click on first two selectable cards)
    const selectableCards = page.locator('#player3 .player-cards img.card.selectable');
    await expect(selectableCards).toHaveCount(12);

    await selectableCards.nth(0).click();
    await selectableCards.nth(1).click();

    // Verify 2 cards are selected
    const selectedCards = page.locator('#player3 .player-cards img.card.selected');
    await expect(selectedCards).toHaveCount(2);

    // Discard button should be enabled now
    const discardBtn = page.locator('#discard-btn');
    await expect(discardBtn).toBeEnabled();

    // Click discard
    await discardBtn.click();

    // Now contract controls should be visible
    await expect(page.locator('#contract-controls')).toBeVisible({ timeout: 5000 });

    // Player should now have 10 cards
    await expect(player3Cards).toHaveCount(10);

    // Announce a contract
    await page.click('#announce-btn');

    // Phase should change to playing
    await expect(page.locator('#play-controls')).toBeVisible({ timeout: 5000 });
  });
});
