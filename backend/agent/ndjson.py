"""Incremental parser for the model's streamed NDJSON claims (one JSON object per line).

Feed it text deltas as they arrive; it returns each claim as soon as its line is complete,
so claims can be validated and sent to the client while the model is still writing.

Tolerated: blank lines, code-fence lines (```), a trailing comma after an object, and a
final line without a newline (handled by :meth:`NdjsonClaimParser.close`). A line that is
not a valid claim object (garbage, or a line cut off when the stream ended) is logged and
skipped; it never fails the answer.
"""

from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from agent.citations import ModelClaim

log = logging.getLogger(__name__)


class NdjsonClaimParser:
    def __init__(self) -> None:
        self._buffer = ""
        self.skipped_lines = 0

    def feed(self, delta: str) -> list[ModelClaim]:
        """Add streamed text; return the claims of every line completed by it."""
        self._buffer += delta
        *lines, self._buffer = self._buffer.split("\n")
        return self._parse_lines(lines)

    def close(self) -> list[ModelClaim]:
        """End of stream: parse whatever is left in the buffer."""
        rest, self._buffer = self._buffer, ""
        return self._parse_lines([rest])

    def _parse_lines(self, lines: list[str]) -> list[ModelClaim]:
        claims = []
        for line in lines:
            claim = self._parse_line(line)
            if claim is not None:
                claims.append(claim)
        return claims

    def _parse_line(self, line: str) -> ModelClaim | None:
        line = line.strip().removesuffix(",").strip()
        if not line or line.startswith("```"):
            return None
        try:
            return ModelClaim.model_validate(json.loads(line))
        except (json.JSONDecodeError, ValidationError):
            self.skipped_lines += 1
            log.warning("skipped malformed claim line: %.200s", line)
            return None
