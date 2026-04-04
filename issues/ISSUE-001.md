# ISSUE-001: Core Transcription Engine (AFK)

## Parent PRD
PRD-001

## What to build
Implement the logic to transcribe audio using Gemini and store it in the `AgentState`. This will be a new node in the LangGraph workflow.

## Acceptance criteria
- [ ] Update `AgentState` in `graph_engine.py` to include `transcription` and `should_transcribe` fields.
- [ ] Implement `transcribe_node` in `graph_engine.py`.
- [ ] Connect `transcribe_node` to the workflow (e.g., after `download` or in parallel with `summarize`).
- [ ] Add logic to `transcribe_node` to use Gemini 1.5 Flash (or relevant model) for full audio transcription.
- [ ] Verify transcription is stored in the state after processing.

## Blocked by
None - can start immediately

## User stories addressed
Reference by number from the parent PRD:
- As a user, I want to see the full transcript of a podcast episode so I can refer back to specific details.
