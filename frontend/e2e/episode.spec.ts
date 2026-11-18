import { expect, test } from "@playwright/test";
import { SEED } from "./seed.mjs";

const { episode } = SEED;

test("episode page: verdict first, TL;DR tab, streamed cited answer", async ({ page }, testInfo) => {
  await page.goto(`/episode/${episode.id}`);

  // Server-rendered metadata from the mock API.
  await expect(page.getByRole("heading", { level: 1, name: episode.title })).toBeVisible();

  // The worth-it verdict comes first and loads on its own.
  const verdict = page.getByTestId("verdict-panel");
  await expect(verdict.getByRole("heading", { name: "Is this worth my time?" })).toBeVisible();
  await expect(verdict.getByText("Founders building AI products")).toBeVisible();
  const verdictTop = (await verdict.boundingBox())?.y ?? Infinity;
  const tabsTop = (await page.getByRole("tablist", { name: "Summaries" }).boundingBox())?.y ?? -Infinity;
  expect(verdictTop).toBeLessThan(tabsTop);

  // One click opens the TL;DR.
  await page.getByRole("tab", { name: "TL;DR" }).click();
  await expect(page.getByRole("tabpanel", { name: "TL;DR" }).getByText("They debate how AI regulation will land")).toBeVisible();

  // Ask a question; claims stream in before the answer completes.
  await page.getByLabel("Your question").fill("Summarize for a startup founder");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const answer = page.getByTestId("ask-answer");
  await expect(answer.getByText("Ship before you scale")).toBeVisible();
  await expect(answer.getByTestId("answer-footer")).toHaveCount(0);
  await expect(answer.getByText("Hire for curiosity")).toBeVisible();
  await expect(answer.getByTestId("answer-footer")).toContainText("mock-model");

  const citation = answer.getByTestId("citation").first();
  await expect(citation).toHaveText(/1:02/);
  await expect(citation).toHaveAttribute("href", `${episode.audio_url}#t=62`);
  await expect(citation).toHaveAttribute("target", "_blank");
  await expect(citation).toHaveAttribute("rel", "noopener noreferrer");
  await expect(answer.getByTestId("citation").nth(1)).toHaveAttribute("href", `${episode.audio_url}#t=3661`);

  // Chat is a 501 stub on the backend for now: a calm notice, not a crash.
  await expect(page.getByTestId("chat-unavailable")).toContainText("Chat is not available yet");

  await page.screenshot({ path: testInfo.outputPath("episode-desktop.png"), fullPage: true });

  // Responsive down to 360px: no horizontal scrolling.
  await page.setViewportSize({ width: 360, height: 800 });
  const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(scrollWidth).toBeLessThanOrEqual(360);
  await page.screenshot({ path: testInfo.outputPath("episode-360.png"), fullPage: true });
});
