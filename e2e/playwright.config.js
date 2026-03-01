// @ts-check
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests',

  // Run tests sequentially - parallel execution causes state conflicts with shared server
  fullyParallel: false,
  workers: 1,

  // Fail the build on CI if you accidentally left test.only in the source code
  forbidOnly: !!process.env.CI,

  // Retry on CI only
  retries: process.env.CI ? 2 : 0,

  // Reporter to use
  reporter: 'html',

  // Shared settings for all projects
  use: {
    // Base URL to use in actions like `await page.goto('/')`
    // Use port 3001 for E2E tests to avoid conflicts with dev server
    baseURL: 'http://127.0.0.1:3001',

    // Collect trace when retrying the failed test
    trace: 'on-first-retry',

    // Take screenshot on failure
    screenshot: 'only-on-failure',
  },

  // Configure projects for browsers
  projects: [
    {
      name: 'chromium',
      use: { },
    },
  ],

  // Run Flask server before starting the tests on port 3001 (to avoid conflicts with dev server)
  webServer: {
    command: 'cd ../server && source ../venv/bin/activate && FLASK_DEBUG=0 FLASK_PORT=3001 python preferans_server.py',
    url: 'http://127.0.0.1:3001/api/health',
    reuseExistingServer: !process.env.CI,  // Reuse existing server locally to avoid port conflicts
    timeout: 30000,
  },
});
