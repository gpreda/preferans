// @ts-check
const { test, expect } = require('@playwright/test');
const { DSLRunner, completeExchangeWithDragDrop } = require('../lib/dsl-runner');

/**
 * Helper to get to contract phase (after exchange)
 */
async function setupToContractPhase(page) {
  await page.goto('/?e2e');
  await expect(page.locator('#message-area.success')).toBeVisible({ timeout: 10000 });

  await page.click('#new-game-btn');
  await page.waitForTimeout(500);

  const runner = new DSLRunner(page);
  await runner.passAuction();

  // Check if Player 3 is declarer
  const player3IsDeclarer = await page.locator('#player3 .player-role').textContent()
    .then(t => /declarer/i.test(t || ''))
    .catch(() => false);

  if (player3IsDeclarer) {
    await completeExchangeWithDragDrop(page);
    await expect(page.locator('#contract-controls')).toBeVisible({ timeout: 5000 });
  }

  return player3IsDeclarer;
}

test.describe('Contract Selection', () => {

  test('contract panel shows after commit exchange', async ({ page }) => {
    const isHumanDeclarer = await setupToContractPhase(page);

    if (isHumanDeclarer) {
      await expect(page.locator('#contract-controls')).toBeVisible();
    }
  });

  test('contract level selector visible', async ({ page }) => {
    const isHumanDeclarer = await setupToContractPhase(page);

    if (isHumanDeclarer) {
      const levelSelect = page.locator('#contract-level');
      await expect(levelSelect).toBeVisible();
    }
  });

  test('contract level has valid options', async ({ page }) => {
    const isHumanDeclarer = await setupToContractPhase(page);

    if (isHumanDeclarer) {
      const levelSelect = page.locator('#contract-level');
      const options = levelSelect.locator('option');
      const count = await options.count();

      // Should have at least one level option
      expect(count).toBeGreaterThan(0);
    }
  });

  test('announce button visible in contract phase', async ({ page }) => {
    const isHumanDeclarer = await setupToContractPhase(page);

    if (isHumanDeclarer) {
      await expect(page.locator('#announce-btn')).toBeVisible();
    }
  });

  test('announce button transitions to playing phase', async ({ page }) => {
    const isHumanDeclarer = await setupToContractPhase(page);

    if (isHumanDeclarer) {
      await page.click('#announce-btn');
      await expect(page.locator('#play-controls')).toBeVisible({ timeout: 5000 });
    }
  });

  test('phase indicator shows PLAYING after announce', async ({ page }) => {
    const isHumanDeclarer = await setupToContractPhase(page);

    if (isHumanDeclarer) {
      await page.click('#announce-btn');
      await expect(page.locator('#phase-indicator')).toHaveText(/play/i, { timeout: 5000 });
    }
  });

  test('can select different contract levels', async ({ page }) => {
    const isHumanDeclarer = await setupToContractPhase(page);

    if (isHumanDeclarer) {
      const levelSelect = page.locator('#contract-level');
      const options = levelSelect.locator('option');
      const count = await options.count();

      if (count > 1) {
        // Select the second option
        const secondOptionValue = await options.nth(1).getAttribute('value');
        await levelSelect.selectOption(secondOptionValue);

        // Verify selection
        const selectedValue = await levelSelect.inputValue();
        expect(selectedValue).toBe(secondOptionValue);
      }
    }
  });

  test('contract controls hidden after announce', async ({ page }) => {
    const isHumanDeclarer = await setupToContractPhase(page);

    if (isHumanDeclarer) {
      await page.click('#announce-btn');
      await page.waitForTimeout(500);

      // Contract controls should be hidden after announcing
      await expect(page.locator('#contract-controls')).toBeHidden();
    }
  });

  test('exchange controls hidden after commit', async ({ page }) => {
    const isHumanDeclarer = await setupToContractPhase(page);

    if (isHumanDeclarer) {
      // Exchange controls should be hidden once we're in contract phase
      await expect(page.locator('#exchange-controls')).toBeHidden();
    }
  });

});
