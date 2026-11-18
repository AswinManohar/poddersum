---
name: poddersum-ask
description: Answer questions about a specific podcast episode with PodderSum, grounded in its transcript with timestamp links. Use for follow-up questions after a PodderSum summary ("what did they say about X?", "who was the guest?", "where do they talk about pricing?", "explain the part about sleep") and for a back-and-forth chat about one episode. Works with no API key, using your own model if needed. For a first summary of an episode use poddersum-summarize instead.
metadata: {"openclaw": {"requires": {"anyBins": ["python3", "python"]}, "homepage": "https://github.com/AswinManohar/poddersum"}}
---

# PodderSum: ask about an episode

Answers come only from the episode's transcript. Every claim cites a moment in the audio as
a `[m:ss](audio_url#t=seconds)` link. **No API key is needed.** The answer is written either
by PodderSum's server-side model or by **you** from the transcript.

## Which interface to use

1. **MCP tools present** (`ask_episode`, `chat_episode`, `get_transcript` from the
   `poddersum` server, e.g. `mcp__poddersum__ask_episode`): use them.
2. **Otherwise** run the bundled stdlib script, `scripts/poddersum.py` inside this skill's
   folder (OpenClaw: `{baseDir}/scripts/poddersum.py`):

   ```bash
   python3 scripts/poddersum.py ask <episode_id> "What did they say about sleep?"
   python3 scripts/poddersum.py chat <episode_id> "And what did the second guest think?"
   python3 scripts/poddersum.py transcript --episode-id <episode_id> --start-ms 300000 --end-ms 600000
   ```

   Exit code 0 = done (an answer, or a transcript for you to answer from), 3 = still
   transcribing (run again later), 1 = error.

## Workflow

1. **Find the episode_id.** Reuse the one from the earlier PodderSum summary in this
   conversation. If there is none, resolve the episode first: call `summarize_episode`
   (or `search_podcasts` + `list_episodes`) and confirm with the user if it is ambiguous.
2. **If you already read this episode's transcript** in this conversation (host-model
   summary), answer from it directly, using the citation rules below. Fetch a specific
   stretch with `get_transcript(episode_id, start_ms, end_ms)` if needed.
3. **Otherwise choose the tool.**
   - `chat_episode(episode_id, message)` for a conversation: the server remembers earlier
     turns for the episode, so short follow-ups ("and the second guest?") work.
   - `ask_episode(episode_id, question)` for one self-contained question (no memory). Make
     the question standalone, e.g. "What does the guest say about remote work?".
   - Pass `engine="host"` when the user wants their own model to answer.
4. **Status**: `ok` = present it. `transcript` = answer yourself from the segments (below).
   `processing` = the transcript is not ready yet (tell the user, call again with the same
   id). "API not reachable" = the user must start PodderSum.
5. **Present** the answer as short bullets, keeping every timestamp link as returned. If the
   transcript does not cover the question, say so. Never answer from your own knowledge and
   present it as the episode's content.

## Answering yourself (status=transcript)

Segments look like `[segment_id] m:ss (t=seconds) Speaker: text`. Read further pages with
`get_transcript(..., start_ms=next_start_ms)` while `next_start_ms` is set. End every claim
with `[m:ss](<audio_url>#t=<seconds>)`, copied from the start of a supporting segment (use
the `link_base` given). Never invent or round a timestamp. Drop claims you cannot tie to a
segment.
