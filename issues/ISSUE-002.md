# ISSUE-002: Markdown Export and Batch Support (AFK)

## Parent PRD
PRD-001

## What to build
Extend the batch processor to handle the transcription state and save it as a Markdown file.

## Acceptance criteria
- [ ] Update `main.py` to check for `transcription` in the final state.
- [ ] Create a `transcriptions/` directory if it doesn't exist.
- [ ] Save the transcript as a Markdown file with a consistent naming convention (similar to summaries).
- [ ] Add a CLI flag or configuration in `main.py` to toggle transcription on/off.

## Blocked by
- Blocked by ISSUE-001

## User stories addressed
Reference by number from the parent PRD:
- As a user, I want the transcript to be saved as a Markdown file for easy reading and archiving.
