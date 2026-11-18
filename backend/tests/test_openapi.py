"""The exported OpenAPI schema carries the whole contract v0. No DB needed."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from api.schemas import CONTRACT_MODELS
from scripts.export_openapi import build_schema
from tests.conftest import BACKEND_DIR


@pytest.fixture(scope="module")
def schema() -> dict:
    return build_schema()


def _ref(name: str) -> dict:
    return {"$ref": f"#/components/schemas/{name}"}


def test_all_contract_models_in_components(schema: dict) -> None:
    components = schema["components"]["schemas"]
    expected = {m.__name__ for m in CONTRACT_MODELS} | {
        "AnswerStreamEvent",
        "EpisodeStatus",
        "JobState",
        "JobStep",
        "ArtifactKind",
        "VerdictSection",
        "ChatRole",
    }
    assert expected <= set(components), expected - set(components)
    # no FastAPI input/output split names leaking into the frontend types
    assert not [n for n in components if n.endswith(("-Input", "-Output"))]


ROUTES = {
    ("get", "/search"): ("200", "application/json", _ref("SearchResponse")),
    ("get", "/podcasts/{podcast_id}/episodes"): (
        "200",
        "application/json",
        _ref("PodcastEpisodes"),
    ),
    ("get", "/episodes/{episode_id}"): ("200", "application/json", _ref("EpisodeDetail")),
    ("post", "/episodes/{episode_id}/ingest"): ("202", "application/json", _ref("JobStatus")),
    ("get", "/jobs/{job_id}"): ("200", "application/json", _ref("JobStatus")),
    ("get", "/jobs/{job_id}/events"): ("200", "text/event-stream", _ref("JobEvent")),
    ("post", "/episodes/{episode_id}/ask"): (
        "200",
        "text/event-stream",
        _ref("AnswerStreamEvent"),
    ),
    ("get", "/episodes/{episode_id}/artifacts/{kind}"): (
        "200",
        "application/json",
        _ref("Artifact"),
    ),
    ("post", "/episodes/{episode_id}/chat"): (
        "200",
        "text/event-stream",
        _ref("AnswerStreamEvent"),
    ),
    ("get", "/episodes/{episode_id}/chat"): (
        "200",
        "application/json",
        {"type": "array", "items": _ref("ChatMessage")},
    ),
}


@pytest.mark.parametrize(("method", "path"), list(ROUTES))
def test_route_documented_with_models(schema: dict, method: str, path: str) -> None:
    status, media, expected = ROUTES[(method, path)]
    op = schema["paths"][path][method]
    body = op["responses"][status]["content"][media]["schema"]
    for key, value in expected.items():
        assert body[key] == value
    for code, resp in op["responses"].items():
        if code.startswith(("4", "5")) and code != "422":
            assert resp["content"]["application/json"]["schema"] == _ref("ErrorResponse")


def test_request_bodies(schema: dict) -> None:
    ask = schema["paths"]["/episodes/{episode_id}/ask"]["post"]["requestBody"]
    chat = schema["paths"]["/episodes/{episode_id}/chat"]["post"]["requestBody"]
    assert ask["content"]["application/json"]["schema"] == _ref("AskRequest")
    assert chat["content"]["application/json"]["schema"] == _ref("ChatRequest")


def test_answer_stream_union_is_discriminated(schema: dict) -> None:
    union = schema["components"]["schemas"]["AnswerStreamEvent"]
    assert union["discriminator"]["propertyName"] == "event"
    assert set(union["discriminator"]["mapping"]) == {"status", "claim", "answer", "error", "done"}


def test_claim_requires_citation(schema: dict) -> None:
    claim = schema["components"]["schemas"]["Claim"]
    assert claim["properties"]["citations"]["minItems"] == 1


def test_export_script_writes_json(tmp_path) -> None:
    out = tmp_path / "openapi.json"
    subprocess.run(
        [sys.executable, "-m", "scripts.export_openapi", str(out)],
        cwd=BACKEND_DIR,
        check=True,
        env={"PATH": "", "GEMINI_API_KEY": "", "DATABASE_URL": "postgresql://nobody@127.0.0.1:1/x"},
    )
    data = json.loads(out.read_text())
    assert data["openapi"].startswith("3.")
    assert "AnswerStreamEvent" in data["components"]["schemas"]
