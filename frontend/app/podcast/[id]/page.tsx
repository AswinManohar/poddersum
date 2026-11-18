import type { Metadata } from "next";
import { Suspense } from "react";
import { PodcastView } from "@/components/PodcastView";
import { ListSkeleton } from "@/components/Skeleton";
import { getPodcastEpisodes } from "@/lib/api";

export async function generateMetadata({ params }: PageProps<"/podcast/[id]">): Promise<Metadata> {
  const { id } = await params;
  try {
    const { podcast } = await getPodcastEpisodes(id);
    return { title: podcast.title };
  } catch {
    return { title: "Show" };
  }
}

async function PodcastRoute({ params }: { params: PageProps<"/podcast/[id]">["params"] }) {
  const { id } = await params;
  return <PodcastView podcastId={id} />;
}

export default function PodcastPage({ params }: PageProps<"/podcast/[id]">) {
  return (
    <Suspense fallback={<ListSkeleton rows={6} label="Loading episodes" />}>
      <PodcastRoute params={params} />
    </Suspense>
  );
}
