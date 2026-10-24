import { defineConfig } from "@playwright/test";

// Starts the real FastAPI server (offline mode) and the built Next.js app, then drives the
// three-step demo. The agent step calls the configured model, so it needs its key in ../.env.
export default defineConfig({
  testDir: "e2e",
  timeout: 120_000,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:3000",
    channel: "chrome",
    headless: true,
    viewport: { width: 1500, height: 950 },
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: "uv run python -m commute_agent.api --port 8000",
      cwd: "..",
      url: "http://127.0.0.1:8000/api/status",
      timeout: 120_000,
      reuseExistingServer: false,
    },
    {
      command: "npm run build && npm run start",
      url: "http://127.0.0.1:3000/",
      timeout: 180_000,
      reuseExistingServer: false,
    },
  ],
});
