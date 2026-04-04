# ISSUE-004: DeepEval Infrastructure & Basic Summary Metric (AFK)

## Parent PRD
PRD-002

## What to build
Set up the DeepEval environment and create a basic test script to evaluate a sample summary against its transcript.

## Acceptance criteria
- [ ] Add `deepeval` to `pyproject.toml` or install instructions.
- [ ] Create `tests/eval_summary.py` script.
- [ ] Configure `SummarizationMetric` from DeepEval.
- [ ] Successfully run a test that takes a local transcript (`.md`) and summary (`.md`) and produces a score.

## Blocked by
None - can start immediately

## User stories addressed
- As a developer, I want to know the quality score (0-1) of my summaries to detect regressions.
