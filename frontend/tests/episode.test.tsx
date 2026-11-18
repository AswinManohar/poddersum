import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EpisodeView } from "@/components/EpisodeView";
import { ApiError } from "@/lib/api";
import { episodeDetail, podcast } from "./fixtures";

const getEpisode = vi.fn<typeof import("@/lib/api").getEpisode>();
vi.mock("@/lib/api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/api")>();
  return { ...mod, getEpisode: (...args: Parameters<typeof mod.getEpisode>) => getEpisode(...args) };
});

// The client workspace has its own tests (workspace.test.tsx); here only its props matter.
vi.mock("@/components/EpisodeWorkspace", () => ({
  EpisodeWorkspace: (props: { status: string; activeJobId: string | null; audioUrl: string }) => (
    <div data-testid="episode-workspace" data-status={props.status} data-audio={props.audioUrl} />
  ),
}));

beforeEach(() => {
  getEpisode.mockReset();
});

describe("EpisodeView", () => {
  it("shows metadata, a link back to the show and the original audio link", async () => {
    getEpisode.mockResolvedValue({ ...episodeDetail, has_feed_transcript: true, status: "processing" });
    render(await EpisodeView({ episodeId: episodeDetail.id }));

    expect(screen.getByRole("heading", { level: 1, name: "The AI Episode" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Hard Fork/ })).toHaveAttribute("href", `/podcast/${podcast.id}`);
    expect(screen.getByText("3 Nov 2026")).toBeInTheDocument();
    expect(screen.getByText("1 h 2 min")).toBeInTheDocument();
    expect(screen.getByText("We talk about AI.")).toBeInTheDocument();
    expect(screen.getByText("Transcript in feed")).toBeInTheDocument();
    expect(screen.getByText("Processing")).toBeInTheDocument();

    const audio = screen.getByRole("link", { name: /Open original audio/ });
    expect(audio).toHaveAttribute("href", "https://media.example.com/ep1.mp3");
    expect(audio).toHaveAttribute("target", "_blank");
    expect(audio).toHaveAttribute("rel", "noopener noreferrer");

    const workspace = screen.getByTestId("episode-workspace");
    expect(workspace).toHaveAttribute("data-status", "processing");
    expect(workspace).toHaveAttribute("data-audio", "https://media.example.com/ep1.mp3");
  });

  it("renders a friendly state for an unknown episode", async () => {
    getEpisode.mockRejectedValue(new ApiError(404, "not_found", "Episode not found."));
    render(await EpisodeView({ episodeId: "x" }));
    expect(screen.getByText("We don't know this episode yet")).toBeInTheDocument();
  });
});
