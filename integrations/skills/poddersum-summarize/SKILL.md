---
name: poddersum-summarize
description: Summarize a podcast episode with PodderSum, with every point linked to a timestamp in the audio. Use when the user asks to summarize a podcast or episode ("summarize this podcast for me", "what's this episode about", "is this episode worth listening to", "give me the takeaways from <show>'s latest episode", "TL;DR of <show> episode <n>"), wants a podcast summarized from an angle ("for a founder", "only the parts about sleep"), or pastes an Apple Podcasts, Spotify or podcast RSS link. Works with no API key, using your own model if needed. Not for summarizing PDFs, documents, web articles, videos or meetings.
metadata: {"openclaw": {"requires": {"anyBins": ["python3", "python"]}, "homepage": "https://github.com/AswinManohar/poddersum"}}
---

# PodderSum: summarize a podcast episode

PodderSum finds the episode and gets its transcript. Every claim in the summary cites a
moment in the audio as a `[m:ss](audio_url#t=seconds)` link. **No API key is needed.** The
summary is written either by PodderSum's server-side model, when it has one, or by **you**
(the host model) from the timestamped transcript. Both paths are normal; neither is an error.

## Which interface to use

1. **MCP tools present** (`summarize_episode`, `get_transcript`, `search_podcasts`, ... from
   the `poddersum` server, e.g. `mcp__poddersum__summarize_episode`): use them.
2. **Otherwise** run the bundled stdlib script (Python 3.9+, no installs), located at
   `scripts/poddersum.py` inside this skill's folder (OpenClaw: `{baseDir}/scripts/poddersum.py`):

   ```bash
   python3 scripts/poddersum.py summarize --podcast "Hidden Brain" --episode latest --style tldr
   python3 scripts/poddersum.py transcript --episode-id <id> [--start-ms N]   # read it yourself
   ```

   Exit code 0 = done (a summary, or a transcript for you to summarize), 2 = ambiguous
   episode (candidates printed), 3 = still transcribing, 1 = error (message on stderr).

Both talk to the PodderSum API at `PODDERSUM_API_URL` (default `http://localhost:8010`).

## Workflow

1. **Identify the show and the episode** from the request.
   - Show: the podcast's name ("Hidden Brain", "Hard Fork").
   - Episode: title words, an episode number ("#212", "episode 212"), or "latest" when the
     user says "latest/newest/this week's" or names no episode.
   - Apple Podcasts link: pass it as `podcast_name` / `--podcast` as is.
   - Spotify link, other web page or RSS URL: PodderSum cannot resolve these. Read the show
     name and episode title from the page if you can (or ask the user), then use them.
2. **Pick the style** from the wording:
   - `tldr` (default): "summarize", "what's it about", "TL;DR", "give me the gist".
   - `takeaways`: "takeaways", "key lessons", "what should I remember", "action items".
   - `verdict`: "is it worth listening to", "should I listen", "worth my time".
3. **Angle**: if the user wants a focus or audience ("for a founder", "only the parts about
   sleep"), pass it as `angle` / `--angle`. Do not invent an angle when none was asked for.
4. **Engine**: leave the default `engine="auto"`. Use `engine="host"` (`--engine host`) when
   the user asks to "use my own model", "don't use PodderSum's model", or similar.
5. **Call** `summarize_episode(podcast_name=..., episode_hint=..., style=..., angle=...)`
   (or `episode_id=...` when you already have it). One call does search, episode choice,
   transcription and either the summary or the transcript.
6. **Handle the status**:
   - `ok`: a finished summary with links. Present it (step 7).
   - `transcript`: write the summary yourself. See "Writing the summary yourself" below.
   - `needs_choice`: the show matched but the episode is ambiguous. Show the candidate
     titles (with dates) and ask the user which one; then call again with its `episode_id`.
     If the matched *show* is clearly wrong, use `search_podcasts` and ask the user.
   - `processing`: tell the user it is still being transcribed (give the %), then call
     again with the same `episode_id` (it resumes the same job). Do not loop more than ~3
     times without telling the user.
   - `model_unavailable` (only with `engine="server"`): call `get_transcript` and continue
     as for `transcript`.
   - error "PodderSum API not reachable": tell the user to start the API (the message says
     how). Do not summarize from memory or the web instead.
7. **Present** the result:
   - Start with the episode title, show and date.
   - Short bullets, and keep **every timestamp link exactly as returned**. They are the
     product's point.
   - For `verdict`, use three groups: Who it's for / What's new / Best 10 minutes.
   - End with one line offering follow-ups ("Ask me anything about this episode"), which
     the `poddersum-ask` skill handles with the same `episode_id`.

## Writing the summary yourself (status=transcript)

The result contains the task, the audio URL, and segments formatted as
`[segment_id] m:ss (t=seconds) Speaker: text`.

- If `next_start_ms` is set, read the rest first: `get_transcript(episode_id=...,
  start_ms=next_start_ms)` (script: `transcript --episode-id ... --start-ms N`) until it is
  null. For a very long episode you may summarize part by part.
- Do the task (style + angle) using **only** the transcript. Add nothing from your own
  knowledge.
- **Every claim ends with at least one link** `[m:ss](<audio_url>#t=<seconds>)`. Copy `m:ss`
  and `t` from the start of a segment that supports the claim. Example: segment
  `[11] 1:05 (t=65) ...` becomes `[1:05](https://host/ep.mp3?x=1#t=65)`. Use the `link_base`
  given in the result and keep its query string.
- Never invent, estimate or round a timestamp. If you cannot tie a claim to a segment, drop
  the claim.
- Then present it as in step 7. You may say it was written from the transcript.

## Examples

- "summarize this podcast for me: Hidden Brain latest episode" ->
  `summarize_episode(podcast_name="Hidden Brain", episode_hint="latest", style="tldr")`
- "Is the new Hard Fork worth listening to?" ->
  `summarize_episode(podcast_name="Hard Fork", episode_hint="latest", style="verdict")`
- "Takeaways from Huberman's sleep episode, for a new parent" ->
  `summarize_episode(podcast_name="Huberman Lab", episode_hint="sleep", style="takeaways", angle="for a new parent")`
- "Summarize the latest Hidden Brain with my own model" ->
  `summarize_episode(podcast_name="Hidden Brain", engine="host")`, then write it yourself.
- `https://podcasts.apple.com/us/podcast/hidden-brain/id1028908750` "what's this about?" ->
  `summarize_episode(podcast_name="https://podcasts.apple.com/us/podcast/hidden-brain/id1028908750")`

Script equivalents: `python3 scripts/poddersum.py summarize --podcast "Hard Fork" --style verdict`,
`... summarize --episode-id <id> --engine host`, `... transcript --episode-id <id>`.
