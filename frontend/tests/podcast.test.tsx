import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PodcastView } from "@/components/PodcastView";
import { ApiError } from "@/lib/api";
import { episodeRef, podcast } from "./fixtures";

const getPodcastEpisodes = vi.fn<typeof import("@/lib/api").getPodcastEpisodes>();
vi.mock("@/lib/api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...mod,
    getPodcastEpisodes: (...args: Parameters<typeof mod.getPodcastEpisodes>) => getPodcastEpisodes(...args),
  };
});

beforeEach(() => {
  getPodcastEpisodes.mockReset();
});

describe("PodcastView", () => {
  it("renders the show header and episode list with badges", async () => {
    getPodcastEpisodes.mockResolvedValue({
      podcast,
      total: 120,
      episodes: [
        episodeRef({ id: "1".repeat(64), title: "Plain episode" }),
        episodeRef({ id: "2".repeat(64), title: "Has transcript", has_feed_transcript: true }),
        episodeRef({ id: "3".repeat(64), title: "Processed", status: "ready", duration_s: null, published_at: null }),
      ],
    });
    render(await PodcastView({ podcastId: podcast.id }));

    expect(screen.getByRole("heading", { level: 1, name: "Hard Fork" })).toBeInTheDocument();
    expect(screen.getByText("Newest 3 of 120")).toBeInTheDocument();

    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(3);

    const first = within(items[0]).getByRole("link");
    expect(first).toHaveAttribute("href", `/episode/${"1".repeat(64)}`);
    expect(first).toHaveTextContent("3 Nov 2026");
    expect(first).toHaveTextContent("1 h 2 min");
    expect(within(items[0]).queryByText("Transcript in feed")).not.toBeInTheDocument();
    expect(within(items[0]).queryByText("New")).not.toBeInTheDocument();

    expect(within(items[1]).getByText("Transcript in feed")).toBeInTheDocument();
    expect(within(items[2]).getByText("Ready")).toBeInTheDocument();
  });

  it("asks the user to search first when the show is unknown (404)", async () => {
    getPodcastEpisodes.mockRejectedValue(new ApiError(404, "not_found", "Podcast not found."));
    render(await PodcastView({ podcastId: podcast.id }));
    expect(screen.getByText("We don't know this show yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Search for it" })).toHaveAttribute("href", "/");
  });

  it("shows an error for other failures", async () => {
    getPodcastEpisodes.mockRejectedValue(new ApiError(502, "upstream_error", "Feed could not be fetched."));
    render(await PodcastView({ podcastId: podcast.id }));
    expect(screen.getByRole("alert")).toHaveTextContent("Feed could not be fetched.");
  });
});
