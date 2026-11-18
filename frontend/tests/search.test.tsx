import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SearchBox } from "@/components/SearchBox";
import { ApiError } from "@/lib/api";
import { otherPodcast, podcast } from "./fixtures";

const replace = vi.fn<(href: string, opts?: { scroll?: boolean }) => void>();
let params = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn(), back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => params,
}));

const searchPodcasts = vi.fn<typeof import("@/lib/api").searchPodcasts>();
vi.mock("@/lib/api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/api")>();
  return { ...mod, searchPodcasts: (...args: Parameters<typeof mod.searchPodcasts>) => searchPodcasts(...args) };
});

beforeEach(() => {
  replace.mockReset();
  searchPodcasts.mockReset();
  params = new URLSearchParams();
  window.history.replaceState(null, "", "/");
});

describe("SearchBox", () => {
  it("shows results for a typed query, links to the show and mirrors ?q=", async () => {
    searchPodcasts.mockResolvedValue({ query: "hard", results: [podcast, otherPodcast] });
    const user = userEvent.setup();
    render(<SearchBox />);

    await user.type(screen.getByLabelText("Find a podcast"), "hard");

    const results = await screen.findByRole("list");
    const links = within(results).getAllByRole("link");
    expect(links).toHaveLength(2);
    expect(links[0]).toHaveAttribute("href", `/podcast/${podcast.id}`);
    expect(links[0]).toHaveTextContent("Hard Fork");
    expect(links[0]).toHaveTextContent("The New York Times");
    expect(links[0]).toHaveTextContent("Technology");
    expect(searchPodcasts).toHaveBeenLastCalledWith("hard", undefined, expect.anything());
    expect(replace).toHaveBeenCalledWith("/?q=hard", { scroll: false });
  });

  it("searches immediately on Enter", async () => {
    searchPodcasts.mockResolvedValue({ query: "fork", results: [podcast] });
    const user = userEvent.setup();
    render(<SearchBox />);
    await user.type(screen.getByLabelText("Find a podcast"), "fork{Enter}");
    expect(await screen.findByText("Hard Fork")).toBeInTheDocument();
  });

  it("restores the query from the URL on load", async () => {
    params = new URLSearchParams("q=hard fork");
    window.history.replaceState(null, "", "/?q=hard%20fork");
    searchPodcasts.mockResolvedValue({ query: "hard fork", results: [podcast] });
    render(<SearchBox />);
    expect(screen.getByLabelText("Find a podcast")).toHaveValue("hard fork");
    expect(await screen.findByText("Hard Fork")).toBeInTheDocument();
    expect(searchPodcasts).toHaveBeenCalledTimes(1);
  });

  it("shows an empty state when nothing matches", async () => {
    searchPodcasts.mockResolvedValue({ query: "zzz", results: [] });
    const user = userEvent.setup();
    render(<SearchBox />);
    await user.type(screen.getByLabelText("Find a podcast"), "zzz");
    expect(await screen.findByText("No shows found")).toBeInTheDocument();
  });

  it("shows an error state and retries", async () => {
    searchPodcasts.mockRejectedValueOnce(new ApiError(502, "upstream_error", "The podcast directory is unavailable."));
    const user = userEvent.setup();
    render(<SearchBox />);
    await user.type(screen.getByLabelText("Find a podcast"), "hard");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Search failed");
    expect(alert).toHaveTextContent("The podcast directory is unavailable.");

    searchPodcasts.mockResolvedValueOnce({ query: "hard", results: [podcast] });
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Hard Fork")).toBeInTheDocument();
  });

  it("shows nothing before a query is typed", () => {
    render(<SearchBox />);
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
    expect(searchPodcasts).not.toHaveBeenCalled();
  });
});
