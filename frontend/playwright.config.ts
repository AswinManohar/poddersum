/**
 * Playwright end-to-end flow (`npm run e2e`). Not part of `npm test` (Vitest only picks
 * up `tests/**`).
 *
 * Server components fetch the episode on the Next.js server, so `page.route` alone cannot
 * mock the API. Instead a tiny node mock API (`e2e/mock-api.mjs`) is started on a free
 * port and `NEXT_PUBLIC_API_BASE_URL` points both the server and the browser at it.
 */
import { execFileSync } from "node:child_process";
import { defineConfig, devices } from "@playwright/test";

/** Ask the OS for a free TCP port (synchronously, so the config stays a plain object). */
function freePort(): number {
  const script =
    "const s=require('net').createServer();s.listen(0,'127.0.0.1',()=>{process.stdout.write(String(s.address().port));s.close()})";
  return Number(execFileSync(process.execPath, ["-e", script], { encoding: "utf8" }));
}

// Workers re-evaluate this file; they inherit the env set by the main process, so the
// ports are chosen once.
process.env.E2E_API_PORT ??= String(freePort());
process.env.E2E_WEB_PORT ??= String(freePort());
const apiPort = process.env.E2E_API_PORT;
const webPort = process.env.E2E_WEB_PORT;
const API_BASE_URL = `http://127.0.0.1:${apiPort}`;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `node e2e/mock-api.mjs ${apiPort}`,
      url: `${API_BASE_URL}/healthz`,
      reuseExistingServer: false,
      timeout: 15_000,
    },
    {
      command: `npx next dev --port ${webPort} --hostname 127.0.0.1`,
      url: `http://127.0.0.1:${webPort}/`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: { NEXT_PUBLIC_API_BASE_URL: API_BASE_URL, NEXT_TELEMETRY_DISABLED: "1" },
    },
  ],
});
