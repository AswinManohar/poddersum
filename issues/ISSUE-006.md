# ISSUE-006: Batch Evaluation Report (AFK)

## Parent PRD
PRD-002

## What to build
Create a script to iterate through all existing `summaries/` and `transcriptions/` files and generate a CSV/JSON report of their quality scores.

## Acceptance criteria
- [ ] Implement `tests/run_batch_eval.py`.
- [ ] Create a report in `eval_reports/` folder.
- [ ] Ensure the script runs autonomously.

## Blocked by
- Blocked by ISSUE-005

## User stories addressed
- As a developer, I want to compare different summary prompts to see which one performs better.
