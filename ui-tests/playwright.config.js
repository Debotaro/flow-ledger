const { defineConfig } = require("@playwright/test");
const path = require("node:path");

module.exports = defineConfig({
  testDir: ".",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60000,
  expect: { timeout: 10000 },
  reporter: [["list"], ["html", { open: "never" }]],
  webServer: process.env.FLOW_BASE_URL
    ? undefined
    : {
        command: `"${process.env.FLOW_PYTHON || "python"}" -m flowledger --port 8012`,
        cwd: path.resolve(__dirname, ".."),
        url: "http://127.0.0.1:8012/api/health",
        reuseExistingServer: !process.env.CI,
        timeout: 60000,
        env: { FLOW_DEMO_MODE: "1", FLOW_DB: "data/browser-tests.db" },
      },
  use: {
    baseURL: process.env.FLOW_BASE_URL || "http://127.0.0.1:8012",
    browserName: "chromium",
    viewport: { width: 1440, height: 1050 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
