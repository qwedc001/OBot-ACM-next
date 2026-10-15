import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { existsSync } from "node:fs";
const root = fileURLToPath(new URL("../", import.meta.url));
export default defineConfig({
  testDir: "./tests/e2e",
  outputDir: "../.codex/ep-e2e-results",
  fullyParallel: false,
  workers: 1,
  timeout: 60000,
  use: {
    channel:
      process.env.OBOT_EP_BROWSER ||
      (existsSync("C:/Program Files/Google/Chrome/Application/chrome.exe")
        ? "chrome"
        : undefined),
    baseURL: "http://127.0.0.1:8765",
    viewport: { width: 1440, height: 1000 },
    colorScheme: "light",
    trace: "retain-on-failure",
  },
  webServer: {
    command:
      "uv run --extra render --no-sync python backend/tests/web_preview.py",
    cwd: root,
    url: "http://127.0.0.1:8765/api/meta",
    reuseExistingServer: false,
    timeout: 45000,
  },
});
