"""Export the API's OpenAPI schema (no server, no database, no real secrets needed).

Usage (from ``backend/``)::

    uv run python -m scripts.export_openapi            # JSON to stdout
    uv run python -m scripts.export_openapi out.json   # JSON to a file

Regenerate ``frontend/lib/types.ts`` (from the repo root; leaves no files behind)::

    tmp=$(mktemp -d) \
      && (cd backend && uv run python -m scripts.export_openapi "$tmp/openapi.json") \
      && npx --yes openapi-typescript@7 "$tmp/openapi.json" -o frontend/lib/types.ts \
      && rm -rf "$tmp"
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def build_schema() -> dict[str, Any]:
    # The settings module requires GEMINI_API_KEY; the schema does not depend on it.
    os.environ.setdefault("GEMINI_API_KEY", "openapi-export-placeholder")
    from api.main import create_app
    from core.settings import Settings

    app = create_app(Settings(_env_file=None, GEMINI_API_KEY="openapi-export-placeholder"))
    return app.openapi()


def main(argv: list[str]) -> int:
    text = json.dumps(build_schema(), indent=2, sort_keys=False) + "\n"
    if len(argv) > 1:
        Path(argv[1]).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
