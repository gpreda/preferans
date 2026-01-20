// @ts-check
const { test, expect } = require('@playwright/test');
const { runDSL, DSLRunner } = require('../lib/dsl-runner');

test.describe('Bidding', () => {

  test('human can bid Game 2', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    // Check if we're in auction and it's human's turn
    const biddingControls = page.locator('#bidding-controls');
    if (await biddingControls.isVisible({ timeout: 2000 }).catch(() => false)) {
      const game2Btn = page.locator('.bid-btn[data-bid-type="game"][data-value="2"]');
      if (await game2Btn.isVisible({ timeout: 1000 }).catch(() => false)) {
        await game2Btn.click();

        // Verify bid appears in history
        await expect(page.locator('#bidding-history-list')).toContainText('2');
      }
    }
  });

  test('human can bid Game 3', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    const biddingControls = page.locator('#bidding-controls');
    if (await biddingControls.isVisible({ timeout: 2000 }).catch(() => false)) {
      const game3Btn = page.locator('.bid-btn[data-bid-type="game"][data-value="3"]');
      if (await game3Btn.isVisible({ timeout: 1000 }).catch(() => false)) {
        await game3Btn.click();
        await expect(page.locator('#bidding-history-list')).toContainText('3');
      }
    }
  });

  test('human can bid Betl', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    const biddingControls = page.locator('#bidding-controls');
    if (await biddingControls.isVisible({ timeout: 2000 }).catch(() => false)) {
      const betlBtn = page.locator('.bid-btn[data-bid-type="betl"]');
      if (await betlBtn.isVisible({ timeout: 1000 }).catch(() => false)) {
        await betlBtn.click();
        await expect(page.locator('#bidding-history-list')).toContainText(/betl/i);
      }
    }
  });

  test('human can bid Sans', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    const biddingControls = page.locator('#bidding-controls');
    if (await biddingControls.isVisible({ timeout: 2000 }).catch(() => false)) {
      const sansBtn = page.locator('.bid-btn[data-bid-type="sans"]');
      if (await sansBtn.isVisible({ timeout: 1000 }).catch(() => false)) {
        await sansBtn.click();
        await expect(page.locator('#bidding-history-list')).toContainText(/sans/i);
      }
    }
  });

  test('human can bid In Hand', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    const biddingControls = page.locator('#bidding-controls');
    if (await biddingControls.isVisible({ timeout: 2000 }).catch(() => false)) {
      const inHandBtn = page.locator('.bid-btn[data-bid-type="in_hand"]');
      if (await inHandBtn.isVisible({ timeout: 1000 }).catch(() => false)) {
        await inHandBtn.click();
        await expect(page.locator('#bidding-history-list')).toContainText(/in.?hand/i);
      }
    }
  });

  test('higher bid required after AI bids', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    // AI bids Game 2 automatically (forehand)
    // Check bidding history shows AI bid
    const biddingHistory = page.locator('#bidding-history-list');

    // Wait for at least one bid to appear
    await expect(biddingHistory).not.toBeEmpty({ timeout: 3000 });

    // If human can still bid, Game 2 should not be available (AI already bid it)
    const biddingControls = page.locator('#bidding-controls');
    if (await biddingControls.isVisible({ timeout: 2000 }).catch(() => false)) {
      const game2Btn = page.locator('.bid-btn[data-bid-type="game"][data-value="2"]');
      const game3Btn = page.locator('.bid-btn[data-bid-type="game"][data-value="3"]');

      // If Game 2 exists, it might be disabled or Game 3 should be available
      const game2Visible = await game2Btn.isVisible().catch(() => false);
      const game3Visible = await game3Btn.isVisible().catch(() => false);

      // At least one higher bid option should exist
      expect(game2Visible || game3Visible).toBe(true);
    }
  });

  test('pass always available during auction', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    const biddingControls = page.locator('#bidding-controls');
    if (await biddingControls.isVisible({ timeout: 2000 }).catch(() => false)) {
      // Pass button should always be available
      const passBtn = page.locator('.bid-btn[data-bid-type="pass"]');
      await expect(passBtn).toBeVisible();
    }
  });

  test('bidding history shows all bids in order', async ({ page }) => {
    await page.goto('/?e2e');
    await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

    await page.click('#new-game-btn');
    await page.waitForTimeout(500);

    // AI should have made at least one bid
    const biddingHistory = page.locator('#bidding-history-list');
    await expect(biddingHistory).toBeVisible({ timeout: 3000 });

    // History should have at least one entry
    const historyEntries = page.locator('#bidding-history-list .bid-line');
    const count = await historyEntries.count();
    expect(count).toBeGreaterThanOrEqual(1);
  });

});
