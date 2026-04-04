# ISSUE-003: Streamlit UI Integration (AFK/HITL)

## Parent PRD
PRD-001

## What to build
Add a "Transcribe" toggle in the Streamlit UI to request and view transcriptions.

## Acceptance criteria
- [ ] Add a checkbox or toggle in the Streamlit sidebar to enable/disable transcription.
- [ ] Update `app.py` to pass the `should_transcribe` state to the LangGraph workflow.
- [ ] Display the transcription in a dedicated tab or expander in the Streamlit interface.
- [ ] Add a download button for the transcription as a Markdown file.

## Blocked by
- Blocked by ISSUE-001

## User stories addressed
Reference by number from the parent PRD:
- As a user, I want to be able to toggle transcription on/off to manage processing costs.
- As a user, I want to see the full transcript of a podcast episode so I can refer back to specific details.
