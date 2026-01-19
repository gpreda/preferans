// @ts-check
const { test, expect } = require('@playwright/test');
const { runDSL, DSLRunner } = require('../lib/dsl-runner');

/**
 * Helper to setup game and get to exchange phase with human as declarer
 * Retries up to maxAttempts times to get human as declarer
 */
async function setupWithHumanDeclarer(page, maxAttempts = 10) {
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    await page.goto('/');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    const runner = new DSLRunner(page);
    await runner.passAuction();

    const player3IsDeclarer = await page.locator('#player3 .player-role').textContent()
      .then(t => /declarer/i.test(t || ''))
      .catch(() => false);

    if (player3IsDeclarer) {
      return true;
    }
  }
  return false;
}

/**
 * Helper to drag a card from source to target
 */
async function dragCard(page, sourceSelector, targetSelector) {
  const source = page.locator(sourceSelector);
  const target = page.locator(targetSelector);

  await source.dragTo(target);
  await page.waitForTimeout(200);
}

test.describe('Exchange Flow - Drag and Drop', () => {

  test('talon shows face-up cards during exchange', async ({ page }) => {
    await runDSL(page, `
      click:new_game
      pass_auction
      phase:exchange
      talon:2:faceup
    `);
  });

  test('exchange controls visible with commit button initially enabled', async ({ page }) => {
    const isHumanDeclarer = await setupWithHumanDeclarer(page);
    test.skip(!isHumanDeclarer, 'Human was not declarer after multiple attempts');

    const commitBtn = page.locator('#pickup-talon-btn');
    await expect(page.locator('#exchange-controls')).toBeVisible();
    await expect(commitBtn).toBeVisible();
    // Button is enabled because talon has 2 cards (user can discard original talon)
    await expect(commitBtn).toBeEnabled();
    await expect(page.locator('#contract-controls')).toBeHidden();
  });

  test('talon cards are draggable', async ({ page }) => {
    const isHumanDeclarer = await setupWithHumanDeclarer(page);
    test.skip(!isHumanDeclarer, 'Human was not declarer after multiple attempts');

    const talonCards = page.locator('.talon-cards img.card.exchange-draggable');
    await expect(talonCards).toHaveCount(2);

    // Check that cards have draggable attribute
    for (let i = 0; i < 2; i++) {
      const draggable = await talonCards.nth(i).getAttribute('draggable');
      expect(draggable).toBe('true');
    }
  });

  test('hand cards are draggable during exchange', async ({ page }) => {
    const isHumanDeclarer = await setupWithHumanDeclarer(page);
    test.skip(!isHumanDeclarer, 'Human was not declarer after multiple attempts');

    const handCards = page.locator('#player3 .player-cards img.card.exchange-draggable');
    await expect(handCards).toHaveCount(10);
  });

  test('drag talon card to hand increases hand size', async ({ page }) => {
    const isHumanDeclarer = await setupWithHumanDeclarer(page);
    test.skip(!isHumanDeclarer, 'Human was not declarer after multiple attempts');

    const handCards = page.locator('#player3 .player-cards img.card');
    await expect(handCards).toHaveCount(10);

    // Drag first talon card to hand
    await dragCard(page, '.talon-cards img.card:first-child', '#player3 .player-cards');

    // Hand should now have 11 cards
    await expect(handCards).toHaveCount(11, { timeout: 5000 });

    // Talon should have 1 card
    const talonCards = page.locator('.talon-cards img.card');
    await expect(talonCards).toHaveCount(1);
  });

  test('drag hand card to talon increases talon size', async ({ page }) => {
    const isHumanDeclarer = await setupWithHumanDeclarer(page);
    test.skip(!isHumanDeclarer, 'Human was not declarer after multiple attempts');

    // First drag both talon cards to hand
    await dragCard(page, '.talon-cards img.card:first-child', '#player3 .player-cards');
    await dragCard(page, '.talon-cards img.card:first-child', '#player3 .player-cards');

    const handCards = page.locator('#player3 .player-cards img.card');
    await expect(handCards).toHaveCount(12, { timeout: 5000 });

    // Talon should be empty (showing placeholder)
    const talonCards = page.locator('.talon-cards img.card');
    await expect(talonCards).toHaveCount(0);

    // Now drag a hand card to talon
    await dragCard(page, '#player3 .player-cards img.card:first-child', '.talon-cards');

    // Hand should have 11 cards
    await expect(handCards).toHaveCount(11, { timeout: 5000 });

    // Talon should have 1 card
    await expect(talonCards).toHaveCount(1);
  });

  test('commit button disabled when talon != 2 cards', async ({ page }) => {
    const isHumanDeclarer = await setupWithHumanDeclarer(page);
    test.skip(!isHumanDeclarer, 'Human was not declarer after multiple attempts');

    const commitBtn = page.locator('#pickup-talon-btn');

    // Initially talon has 2 cards - button should be enabled
    await expect(commitBtn).toBeEnabled();

    // Drag one talon card to hand (talon now has 1 card)
    await dragCard(page, '.talon-cards img.card:first-child', '#player3 .player-cards');
    await expect(commitBtn).toBeDisabled();

    // Drag other talon card to hand (talon now has 0 cards)
    await dragCard(page, '.talon-cards img.card:first-child', '#player3 .player-cards');
    await expect(commitBtn).toBeDisabled();

    // Drag one hand card to talon (talon now has 1 card)
    await dragCard(page, '#player3 .player-cards img.card:first-child', '.talon-cards');
    await expect(commitBtn).toBeDisabled();
  });

  test('commit button enabled when talon == 2 cards', async ({ page }) => {
    const isHumanDeclarer = await setupWithHumanDeclarer(page);
    test.skip(!isHumanDeclarer, 'Human was not declarer after multiple attempts');

    const commitBtn = page.locator('#pickup-talon-btn');

    // Drag both talon cards to hand
    await dragCard(page, '.talon-cards img.card:first-child', '#player3 .player-cards');
    await dragCard(page, '.talon-cards img.card:first-child', '#player3 .player-cards');

    // Drag two hand cards to talon
    await dragCard(page, '#player3 .player-cards img.card:first-child', '.talon-cards');
    await dragCard(page, '#player3 .player-cards img.card:first-child', '.talon-cards');

    // Now talon has 2 cards - commit should be enabled
    await expect(commitBtn).toBeEnabled({ timeout: 5000 });
  });

  test('commit exchange finalizes with correct cards', async ({ page }) => {
    const isHumanDeclarer = await setupWithHumanDeclarer(page);
    test.skip(!isHumanDeclarer, 'Human was not declarer after multiple attempts');

    // Drag both talon cards to hand
    await dragCard(page, '.talon-cards img.card:first-child', '#player3 .player-cards');
    await dragCard(page, '.talon-cards img.card:first-child', '#player3 .player-cards');

    // Drag two hand cards to talon
    await dragCard(page, '#player3 .player-cards img.card:first-child', '.talon-cards');
    await dragCard(page, '#player3 .player-cards img.card:first-child', '.talon-cards');

    // Click commit
    const commitBtn = page.locator('#pickup-talon-btn');
    await expect(commitBtn).toBeEnabled({ timeout: 5000 });
    await commitBtn.click();

    // Contract controls should be visible
    await expect(page.locator('#contract-controls')).toBeVisible({ timeout: 5000 });

    // Exchange controls should be hidden
    await expect(page.locator('#exchange-controls')).toBeHidden();

    // Hand should have 10 cards
    const handCards = page.locator('#player3 .player-cards img.card');
    await expect(handCards).toHaveCount(10, { timeout: 5000 });
  });

  test('can drag card back from talon to hand', async ({ page }) => {
    const isHumanDeclarer = await setupWithHumanDeclarer(page);
    test.skip(!isHumanDeclarer, 'Human was not declarer after multiple attempts');

    // Drag both talon cards to hand
    await dragCard(page, '.talon-cards img.card:first-child', '#player3 .player-cards');
    await dragCard(page, '.talon-cards img.card:first-child', '#player3 .player-cards');

    const handCards = page.locator('#player3 .player-cards img.card');
    await expect(handCards).toHaveCount(12, { timeout: 5000 });

    // Drag one hand card to talon
    await dragCard(page, '#player3 .player-cards img.card:first-child', '.talon-cards');
    await expect(handCards).toHaveCount(11);

    // Drag it back to hand
    await dragCard(page, '.talon-cards img.card:first-child', '#player3 .player-cards');
    await expect(handCards).toHaveCount(12);
  });

  test('hand can have 10, 11, or 12 cards during exchange', async ({ page }) => {
    const isHumanDeclarer = await setupWithHumanDeclarer(page);
    test.skip(!isHumanDeclarer, 'Human was not declarer after multiple attempts');

    const handCards = page.locator('#player3 .player-cards img.card');

    // Start with 10 cards
    await expect(handCards).toHaveCount(10);

    // Drag one talon card to hand - 11 cards
    await dragCard(page, '.talon-cards img.card:first-child', '#player3 .player-cards');
    await expect(handCards).toHaveCount(11);

    // Drag second talon card to hand - 12 cards
    await dragCard(page, '.talon-cards img.card:first-child', '#player3 .player-cards');
    await expect(handCards).toHaveCount(12);
  });

  test('full exchange flow completes correctly', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    // Use DSL runner to pass through auction
    const runner = new DSLRunner(page);
    await runner.passAuction();

    await expect(page.locator('#exchange-controls')).toBeVisible({ timeout: 10000 });

    // Check if Player 3 is declarer
    const player3IsDeclarer = await page.locator('#player3 .player-role').textContent()
      .then(t => /declarer/i.test(t || ''))
      .catch(() => false);

    if (player3IsDeclarer) {
      // Human is declarer - use drag and drop to complete exchange
      const handCards = page.locator('#player3 .player-cards img.card');
      await expect(handCards).toHaveCount(10, { timeout: 5000 });

      // Drag both talon cards to hand
      await dragCard(page, '.talon-cards img.card:first-child', '#player3 .player-cards');
      await dragCard(page, '.talon-cards img.card:first-child', '#player3 .player-cards');
      await expect(handCards).toHaveCount(12);

      // Drag two hand cards to talon
      await dragCard(page, '#player3 .player-cards img.card:first-child', '.talon-cards');
      await dragCard(page, '#player3 .player-cards img.card:first-child', '.talon-cards');
      await expect(handCards).toHaveCount(10);

      // Commit the exchange
      const commitBtn = page.locator('#pickup-talon-btn');
      await expect(commitBtn).toBeEnabled();
      await commitBtn.click();

      await expect(page.locator('#contract-controls')).toBeVisible({ timeout: 5000 });
      await expect(handCards).toHaveCount(10);

      await page.click('#announce-btn');

      await expect(page.locator('#play-controls')).toBeVisible({ timeout: 5000 });
    } else {
      // AI is declarer - verify exchange UI state
      const declarerCards = page.locator('.player:has(.player-role)').filter({ hasText: /declarer/i }).locator('.player-cards img.card');
      await expect(declarerCards).toHaveCount(12, { timeout: 5000 });
    }
  });
});
