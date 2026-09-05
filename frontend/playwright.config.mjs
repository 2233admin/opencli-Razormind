import { defineConfig } from "@playwright/test";

const port = process.env.PLAYWRIGHT_SMOKE_PORT ?? process.env.PLAYWRIGHT_TEST_PORT ?? "3101";
if (!/^[1-9]\d{0,4}$/.test(port) || Number(port) > 65535) {
  throw new Error("PLAYWRIGHT_SMOKE_PORT must be an integer from 1 through 65535");
}

const isolatedStudioUrl = process.env.STUDIO_TRACER_URL

export default defineConfig({
  testDir: "./e2e",
  // These suites start isolated fixture servers through their own configs.
  testIgnore: ['**/report-content.spec.mjs', '**/openalice-workspace.spec.mjs'],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    browserName: "chromium",
  },
  webServer: {
    command: "node scripts/start-standalone.mjs",
    url: `http://127.0.0.1:${port}`,
    env: {
      ...process.env,
      HOSTNAME: "127.0.0.1",
      PORT: port,
    },
    reuseExistingServer: process.env.PLAYWRIGHT_REUSE_EXISTING_SERVER === "1",
    timeout: 30_000,
  },
});
