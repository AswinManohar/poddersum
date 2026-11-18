/**
 * Friendly aliases over the generated OpenAPI types (`lib/types.ts`, do not edit that file).
 * Regenerate types.ts from the backend; only add aliases here.
 */
import type { components } from "./types";

type Schemas = components["schemas"];

export type PodcastHit = Schemas["PodcastHit"];
export type SearchResponse = Schemas["SearchResponse"];
export type EpisodeRef = Schemas["EpisodeRef"];
export type EpisodeDetail = Schemas["EpisodeDetail"];
export type EpisodeStatus = Schemas["EpisodeStatus"];
export type PodcastEpisodes = Schemas["PodcastEpisodes"];
export type ErrorResponse = Schemas["ErrorResponse"];
export type TranscriptTag = Schemas["TranscriptTag"];

// Used from M0-S7 (job progress, ask, presets, chat).
export type JobStatus = Schemas["JobStatus"];
export type JobEvent = Schemas["JobEvent"];
export type JobState = Schemas["JobState"];
export type JobStep = Schemas["JobStep"];
export type AskRequest = Schemas["AskRequest"];
export type Answer = Schemas["Answer"];
export type Claim = Schemas["Claim"];
export type Citation = Schemas["Citation"];
export type Artifact = Schemas["Artifact"];
export type ArtifactKind = Schemas["ArtifactKind"];
export type ChatMessage = Schemas["ChatMessage"];
export type ChatRequest = Schemas["ChatRequest"];
export type AnswerStreamEvent = Schemas["AnswerStreamEvent"];
export type VerdictSection = Schemas["VerdictSection"];
