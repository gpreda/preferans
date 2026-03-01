// @ts-check
const { test, expect } = require('@playwright/test');
const { DSLRunner } = require('../lib/dsl-runner');

test.describe('UI State', () => {

  test('phase indicator shows correct phase', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    // Should show auction/bidding or exchange phase
    const phaseIndicator = page.locator('#phase-indicator');
    await expect(phaseIndicator).toHaveText(/auction|bidding|exchange/i);
  });

  test('message area shows game events', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    // Message area should have some content about game start
    const messageArea = page.locator('#message-area');
    const text = await messageArea.textContent();
    expect(text?.length).toBeGreaterThan(0);
  });

  test('language selector visible', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    const languageSelector = page.locator('#language-selector');
    await expect(languageSelector).toBeVisible();
  });

  test('language switch to Serbian works', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    // Select Serbian
    const languageSelector = page.locator('#language-selector');
    await languageSelector.selectOption('SR');
    await page.waitForTimeout(500);

    // Check that some UI text changed
    // New Game button should now be in Serbian
    const newGameBtn = page.locator('#new-game-btn');
    const btnText = await newGameBtn.textContent();

    // Serbian for "New Game" is "Nova Igra"
    expect(btnText).toContain('Nova');
  });

  test('language switch back to English works', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    // Switch to Serbian first
    const languageSelector = page.locator('#language-selector');
    await languageSelector.selectOption('SR');
    await page.waitForTimeout(300);

    // Switch back to English
    await languageSelector.selectOption('EN');
    await page.waitForTimeout(300);

    const newGameBtn = page.locator('#new-game-btn');
    const btnText = await newGameBtn.textContent();
    expect(btnText).toContain('New Game');
  });

  test('player names displayed', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    // All three players should have names displayed
    await expect(page.locator('#player1 .player-name')).toBeVisible();
    await expect(page.locator('#player2 .player-name')).toBeVisible();
    await expect(page.locator('#player3 .player-name')).toBeVisible();
  });

  test('tricks display shows for all players', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    // All players should have tricks display (class is .player-tricks)
    const tricksDisplays = page.locator('.player-tricks');
    await expect(tricksDisplays).toHaveCount(3);
  });

  test('bidding controls visible during auction', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    // During auction, bidding controls should be visible
    const phaseText = await page.locator('#phase-indicator').textContent();

    if (/auction|bidding/i.test(phaseText || '')) {
      await expect(page.locator('#bidding-controls')).toBeVisible();
    }
  });

  test('exchange controls visible during exchange', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    const runner = new DSLRunner(page);
    await runner.passAuction();

    await expect(page.locator('#exchange-controls')).toBeVisible({ timeout: 5000 });
  });

  test('only one control panel visible at a time', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    // Count visible control panels
    const biddingVisible = await page.locator('#bidding-controls').isVisible().catch(() => false);
    const exchangeVisible = await page.locator('#exchange-controls').isVisible().catch(() => false);
    const contractVisible = await page.locator('#contract-controls').isVisible().catch(() => false);
    const playVisible = await page.locator('#play-controls').isVisible().catch(() => false);
    const scoringVisible = await page.locator('#scoring-controls').isVisible().catch(() => false);

    const visibleCount = [biddingVisible, exchangeVisible, contractVisible, playVisible, scoringVisible]
      .filter(v => v).length;

    // At most one should be visible (or none if in transition)
    expect(visibleCount).toBeLessThanOrEqual(1);
  });

  test('talon area visible during exchange', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    const runner = new DSLRunner(page);
    await runner.passAuction();

    await expect(page.locator('#talon')).toBeVisible({ timeout: 5000 });
  });

  test('current trick area exists', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');

    // Current trick area should exist (even if empty)
    await expect(page.locator('#current-trick')).toBeAttached();
  });

  test('header displays game title', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    const header = page.locator('h1');
    await expect(header).toContainText(/preferans/i);
  });

  test('dealer marked with dealer class', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    // One player should be marked as dealer (has .dealer class)
    const dealerPlayer = page.locator('.player.dealer');
    const count = await dealerPlayer.count();

    // Should have exactly one dealer
    expect(count).toBe(1);
  });

  test('card images load correctly', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(1000);

    // Check that card images have valid src
    const player3Cards = page.locator('#player3 .player-cards img.card');
    const firstCard = player3Cards.first();
    const src = await firstCard.getAttribute('src');

    expect(src).toBeTruthy();
    expect(src).toContain('/api/cards/');
  });

  test('bidding history panel visible during auction', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    // Bidding history should be visible
    await expect(page.locator('#bidding-history')).toBeVisible({ timeout: 3000 });
  });

});
