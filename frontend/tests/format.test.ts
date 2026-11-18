import { describe, expect, it } from "vitest";
import { audioLinkAt, formatCost, formatDate, formatDuration, formatTimestamp, stepLabel } from "@/lib/format";

describe("formatDuration", () => {
  it.each([
    [3725, "1 h 2 min"],
    [3600, "1 h"],
    [754, "13 min"],
    [42, "1 min"],
    [7199, "2 h"],
  ])("%i s -> %s", (s, out) => {
    expect(formatDuration(s)).toBe(out);
  });

  it("returns null for missing or non-positive values", () => {
    expect(formatDuration(null)).toBeNull();
    expect(formatDuration(undefined)).toBeNull();
    expect(formatDuration(0)).toBeNull();
    expect(formatDuration(Number.NaN)).toBeNull();
  });
});

describe("formatDate", () => {
  it("formats ISO dates in UTC", () => {
    expect(formatDate("2026-11-03T10:00:00Z")).toBe("3 Nov 2026");
    expect(formatDate("2026-11-03T23:30:00-05:00")).toBe("4 Nov 2026");
  });

  it("returns null for missing or invalid input", () => {
    expect(formatDate(null)).toBeNull();
    expect(formatDate("")).toBeNull();
    expect(formatDate("not a date")).toBeNull();
  });
});

describe("formatTimestamp", () => {
  it.each([
    [0, "0:00"],
    [65_000, "1:05"],
    [3_725_500, "1:02:05"],
    [-10, "0:00"],
  ])("%i ms -> %s", (ms, out) => {
    expect(formatTimestamp(ms)).toBe(out);
  });
});

describe("audioLinkAt", () => {
  it("adds a media fragment in whole seconds", () => {
    expect(audioLinkAt("https://x.test/a.mp3", 61_900)).toBe("https://x.test/a.mp3#t=61");
  });

  it("keeps the query string and replaces an existing fragment", () => {
    expect(audioLinkAt("https://x.test/a.mp3?k=1#t=5", 2000)).toBe("https://x.test/a.mp3?k=1#t=2");
  });
});

describe("audioLinkAt edge cases", () => {
  it.each([
    ["https://x.test/a.mp3", 0, "https://x.test/a.mp3#t=0"],
    ["https://x.test/a.mp3", 999, "https://x.test/a.mp3#t=0"],
    ["https://x.test/a.mp3", 1000, "https://x.test/a.mp3#t=1"],
    ["https://x.test/a.mp3", 3_725_999, "https://x.test/a.mp3#t=3725"],
    ["https://x.test/a.mp3", -5, "https://x.test/a.mp3#t=0"],
    ["https://x.test/a.mp3", Number.NaN, "https://x.test/a.mp3#t=0"],
    ["https://x.test/a.mp3#", 2000, "https://x.test/a.mp3#t=2"],
    ["https://x.test/a.mp3?u=1&v=2#start", 2000, "https://x.test/a.mp3?u=1&v=2#t=2"],
    ["https://x.test/a.mp3?t=9", 2000, "https://x.test/a.mp3?t=9#t=2"],
  ])("%s at %i ms -> %s", (url, ms, out) => {
    expect(audioLinkAt(url, ms)).toBe(out);
  });
});

describe("formatCost", () => {
  it.each([
    [0.00038, "$0.0004"],
    [0.0123, "$0.0123"],
    [0, "$0.00"],
    [0.00001, "<$0.0001"],
    [1.5, "$1.50"],
  ])("%f -> %s", (usd, out) => {
    expect(formatCost(usd)).toBe(out);
  });
});

describe("stepLabel", () => {
  it("uses plain words and de-snakes unknown steps", () => {
    expect(stepLabel("fetch_transcript")).toBe("Fetching the transcript");
    expect(stepLabel("new_step_name")).toBe("New step name");
  });
});
