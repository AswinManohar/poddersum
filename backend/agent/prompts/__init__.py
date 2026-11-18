"""Versioned prompts, stored as plain text files next to this module.

Each ``<name>.txt`` starts with a ``version: <name>-v<N>`` line and a blank line; the rest
is the prompt text. Bump the version whenever the text changes: preset answers are cached
by version, so a bump is what makes them regenerate.

Prompts:

* ``system``    - shared grounding rules, sent as the system message on every call.
* ``ask``       - task for a user question (``$question`` is substituted); NDJSON output.
* ``tldr``, ``takeaways``, ``verdict`` - preset tasks; JSON output via a schema.

The version stored with an answer (``Answer.prompt_version``, ``artifacts`` and
``requests`` rows) is :func:`answer_version`, which joins the task and system versions,
e.g. ``tldr-v1+system-v1``, so a change to the shared system prompt also invalidates
cached presets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).parent
_HEADER = re.compile(r"^version: (?P<version>[a-z_]+-v\d+)\n\n")


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    text: str

    def render(self, **values: str) -> str:
        """The text with ``$name`` placeholders filled in (all must be given)."""
        return Template(self.text).substitute(values)


@cache
def load_prompt(name: str) -> Prompt:
    """Read ``<name>.txt``; its version must be ``<name>-v<N>``."""
    raw = (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
    match = _HEADER.match(raw)
    if match is None or not match["version"].startswith(f"{name}-v"):
        raise ValueError(f"prompt {name}.txt must start with 'version: {name}-v<N>' + blank line")
    return Prompt(name=name, version=match["version"], text=raw[match.end() :].strip() + "\n")


def system_prompt() -> Prompt:
    return load_prompt("system")


def answer_version(task: Prompt) -> str:
    """The version recorded with an answer made from ``task`` plus the system prompt."""
    return f"{task.version}+{system_prompt().version}"
