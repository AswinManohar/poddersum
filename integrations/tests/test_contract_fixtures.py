"""The recorded API fixtures must match contract v0 (``backend/api/schemas.py``).

The schemas module is loaded read-only by path (it only needs pydantic), so a contract change
in the backend fails here instead of silently drifting from the fake API.
"""

from __future__ import annotations

import importlib.util
import sys

import pytest
from conftest import FIXTURES, REPO, fixture_json
from pydantic import TypeAdapter

SCHEMAS = REPO / "backend" / "api" / "schemas.py"


@pytest.fixture(scope="module")
def schemas():
    if not SCHEMAS.exists():
        pytest.skip("backend/api/schemas.py not found")
    spec = importlib.util.spec_from_file_location("_poddersum_contract_v0", SCHEMAS)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    for model in module.CONTRACT_MODELS:
        model.model_rebuild(_types_namespace=vars(module))
    return module


CASES = [
    ("search.json", "SearchResponse"),
    ("episodes.json", "PodcastEpisodes"),
    ("ingest_job.json", "JobStatus"),
    ("artifact_tldr.json", "Artifact"),
    ("artifact_takeaways.json", "Artifact"),
    ("artifact_verdict.json", "Artifact"),
    ("error_not_found.json", "ErrorResponse"),
    ("error_not_ready.json", "ErrorResponse"),
]


@pytest.mark.parametrize("name,model", CASES)
def test_fixture_matches_contract(schemas, name: str, model: str) -> None:
    getattr(schemas, model).model_validate(fixture_json(name), strict=False)


def test_job_events(schemas) -> None:
    for ev in fixture_json("job_events.json"):
        schemas.JobEvent.model_validate(ev)


@pytest.mark.parametrize("name", ["ask_events.json", "chat_events.json", "ask_error_events.json"])
def test_answer_streams(schemas, name: str) -> None:
    adapter = TypeAdapter(schemas.AnswerStreamEvent)
    events = [adapter.validate_python(e) for e in fixture_json(name)]
    assert events[-1].event == "done"
    assert all(e.event in schemas.ANSWER_STREAM_EVENT_NAMES for e in events)


def test_fake_episode_detail_matches_contract(schemas) -> None:
    from fake_api import FakeState

    state = FakeState()
    for ep in state.episodes:
        schemas.EpisodeDetail.model_validate(state.detail(ep["id"]))


def test_fake_chat_history_matches_contract(schemas, fake_api: str) -> None:
    import json
    import urllib.request

    eid = fixture_json("episodes.json")["episodes"][1]["id"]
    req = urllib.request.Request(
        f"{fake_api}/episodes/{eid}/chat", data=json.dumps({"message": "hi"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )  # fmt: skip
    urllib.request.urlopen(req, timeout=5).read()
    history = json.loads(urllib.request.urlopen(f"{fake_api}/episodes/{eid}/chat", timeout=5).read())
    for msg in history:
        schemas.ChatMessage.model_validate(msg)
    assert [m["role"] for m in history] == ["user", "assistant"]


def test_transcript_fixture_matches_proposed_route(schemas) -> None:
    """``transcript_ep0.json`` follows the PROPOSED transcript route (not in contract v0); its
    segment ids/start times must agree with the citations in the recorded artifacts."""
    from pydantic import BaseModel, Field

    class Segment(BaseModel):
        id: int = Field(ge=1)
        idx: int = Field(ge=0)
        start_ms: int = Field(ge=0)
        end_ms: int = Field(ge=0)
        speaker: str | None = None
        text: str = Field(min_length=1)

    segs = [Segment.model_validate(s) for s in fixture_json("transcript_ep0.json")["segments"]]
    assert all(a.start_ms < b.start_ms for a, b in zip(segs, segs[1:], strict=False))
    starts = {s.id: s.start_ms for s in segs}
    for name in ("artifact_tldr.json", "artifact_takeaways.json", "artifact_verdict.json"):
        for claim in fixture_json(name)["answer"]["claims"]:
            for cite in claim["citations"]:
                assert starts[cite["segment_id"]] == cite["start_ms"], (name, cite)


def test_every_fixture_is_covered() -> None:
    covered = {n for n, _ in CASES} | {"job_events.json", "ask_events.json", "chat_events.json",
                                       "ask_error_events.json", "transcript_ep0.json"}  # fmt: skip
    assert {p.name for p in FIXTURES.glob("*.json")} == covered
