# ISSUE-005: Factual Alignment & Hallucination Detection (AFK)

## Parent PRD
PRD-002

## What to build
Extend the evaluation suite to specifically check if the summary is factually aligned with the podcast content (using the transcript as ground truth).

## Acceptance criteria
- [ ] Configure `FactualAlignmentMetric` or `HallucinationMetric` from DeepEval.
- [ ] Implement evaluation logic that takes a transcript and its summary and detects any fabricated details.
- [ ] Set a "passing" threshold (e.g., 0.8) for these metrics.

## Blocked by
- Blocked by ISSUE-004

## User stories addressed
- As a developer, I want to catch "hallucinations" where the agent invents podcast details.
