import type { Metadata } from "next";
import { Suspense } from "react";
import { EpisodeView } from "@/components/EpisodeView";
import { ListSkeleton } from "@/components/Skeleton";
import { getEpisode } from "@/lib/api";

export async function generateMetadata({ params }: PageProps<"/episode/[id]">): Promise<Metadata> {
  const { id } = await params;
  try {
    const episode = await getEpisode(id);
    return { title: `${episode.title} - ${episode.podcast.title}` };
  } catch {
    return { title: "Episode" };
  }
}

async function EpisodeRoute({ params }: { params: PageProps<"/episode/[id]">["params"] }) {
  const { id } = await params;
  return <EpisodeView episodeId={id} />;
}

export default function EpisodePage({ params }: PageProps<"/episode/[id]">) {
  return (
    <Suspense fallback={<ListSkeleton rows={2} label="Loading episode" />}>
      <EpisodeRoute params={params} />
    </Suspense>
  );
}
