"""``skills/*/scripts/poddersum.py`` (stdlib CLI) against the fake API."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from conftest import (
    CLI_SCRIPT,
    INTEGRATIONS,
    TEST_API_KEY,
    fake_requests,
    fixture_json,
    free_port,
    server_env,
)

EPISODES = fixture_json("episodes.json")["episodes"]
EP_NEW, EP_READY, EP_STALL, EP_FAIL, _ = (e["id"] for e in EPISODES)
PODCAST_ID = fixture_json("episodes.json")["podcast"]["id"]


def run(
    api_url: str, *args: str, api_key: str | None = None, timeout: float = 60
) -> subprocess.CompletedProcess:
    # -I: isolated mode, exactly how an agent would run an untrusted-dir script.
    return subprocess.run(
        [sys.executable, "-I", str(CLI_SCRIPT), *args],
        env=server_env(api_url, api_key),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_scripts_identical_in_every_skill() -> None:
    out = subprocess.run([str(INTEGRATIONS / "build.sh"), "--check"], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_search(fake_api: str) -> None:
    out = run(fake_api, "search", "hidden brain")
    assert out.returncode == 0 and "**Hidden Brain**" in out.stdout and PODCAST_ID in out.stdout


def test_episodes_and_episode(fake_api: str) -> None:
    out = run(fake_api, "episodes", PODCAST_ID, "--limit", "2")
    assert out.returncode == 0 and "1. **The Science of Second Chances**" in out.stdout
    assert "Price of Certainty" not in out.stdout
    one = run(fake_api, "episode", EP_READY)
    assert one.returncode == 0 and "status: ready" in one.stdout


def test_summarize_latest_ingests(fake_api: str) -> None:
    out = run(fake_api, "summarize", "--podcast", "Hidden Brain", "--episode", "latest")
    assert out.returncode == 0, out.stderr
    assert "[1:05](https://audio.example.com/hb/episode-0.mp3?src=feed#t=65)" in out.stdout
    assert "transcribe" in out.stderr  # progress lines
    assert any(r["path"].endswith("/ingest") for r in fake_requests(fake_api))


def test_summarize_json_verdict(fake_api: str) -> None:
    out = run(fake_api, "--json", "summarize", "--episode-id", EP_READY, "--style", "verdict")
    data = json.loads(out.stdout)
    assert data["status"] == "ok" and data["source"] == "artifact"
    assert {c["section"] for c in data["answer"]["claims"]} == {"who_its_for", "whats_new", "best_10_minutes"}
    assert data["answer"]["claims"][0]["citations"][0]["url"].endswith("#t=20")


def test_summarize_angle(fake_api: str) -> None:
    out = run(fake_api, "summarize", "--episode-id", EP_READY, "--angle", "only the parts about sleep")
    assert out.returncode == 0 and "angle: only the parts about sleep" in out.stdout
    asks = [r for r in fake_requests(fake_api) if r["path"].endswith("/ask")]
    assert "only the parts about sleep" in asks[0]["body"]["question"]


def test_summarize_ambiguous_exit_2(fake_api: str) -> None:
    out = run(fake_api, "summarize", "--podcast", "Hidden Brain", "--episode", "rest")
    assert out.returncode == 2 and "Rethinking Rest: Part 1" in out.stdout and "Ask the user" in out.stdout
    exact = run(fake_api, "summarize", "--podcast", "Hidden Brain", "--episode", "rest part 2")
    assert exact.returncode == 0, exact.stdout


def test_summarize_processing_exit_3(fake_api: str) -> None:
    out = run(fake_api, "summarize", "--episode-id", EP_STALL, "--wait", "2")
    assert out.returncode == 3 and "Still transcribing" in out.stdout


def test_summarize_failed_exit_1(fake_api: str) -> None:
    out = run(fake_api, "summarize", "--episode-id", EP_FAIL)
    assert out.returncode == 1 and "HTTP 403" in out.stderr


def test_ask_and_chat_and_job(fake_api: str) -> None:
    ask = run(fake_api, "ask", EP_READY, "What about founders?", api_key=TEST_API_KEY)
    assert ask.returncode == 0 and "For founders" in ask.stdout
    chat = run(fake_api, "chat", EP_READY, "And the second guest?")
    assert chat.returncode == 0 and "labor economist" in chat.stdout
    err = run(fake_api, "ask", EP_READY, "[fail] x")
    assert err.returncode == 1 and "does not cover this question" in err.stderr
    reqs = fake_requests(fake_api)
    assert next(r for r in reqs if r["path"].endswith("/ask"))["authorization"] == f"Bearer {TEST_API_KEY}"
    stalled = run(fake_api, "--json", "summarize", "--episode-id", EP_STALL, "--wait", "1")
    job_id = json.loads(stalled.stdout)["job"]["job_id"]
    job = run(fake_api, "job", job_id)
    assert job.returncode == 0 and "running" in job.stdout


@pytest.mark.parametrize("link,needle", [
    ("https://open.spotify.com/episode/abc", "Spotify links cannot be resolved"),
    ("https://example.com/feed.xml", "searches by show name"),
])  # fmt: skip
def test_unsupported_links(fake_api: str, link: str, needle: str) -> None:
    out = run(fake_api, "summarize", "--podcast", link)
    assert out.returncode == 1 and needle in out.stderr


def test_api_down() -> None:
    url = f"http://127.0.0.1:{free_port()}"
    out = run(url, "search", "x")
    assert out.returncode == 1 and f"PodderSum API not reachable at {url}" in out.stderr
    as_json = run(url, "--json", "search", "x")
    assert json.loads(as_json.stdout)["code"] == "api_unreachable"


# --- host-model mode -------------------------------------------------------------------


def test_transcript_command(fake_api: str) -> None:
    out = run(fake_api, "transcript", "--episode-id", EP_NEW)
    assert out.returncode == 0, out.stderr
    assert "[11] 1:05 (t=65) Shankar Vedantam: Researchers keep finding" in out.stdout
    assert "https://audio.example.com/hb/episode-0.mp3?src=feed#t=<t>" in out.stdout
    page = json.loads(
        run(fake_api, "--json", "transcript", "--podcast", "Hidden Brain", "--max-chars", "1000").stdout
    )
    nxt = page["transcript"]["next_start_ms"]
    assert (
        nxt
        and f"--start-ms {nxt}"
        in run(fake_api, "transcript", "--episode-id", EP_NEW, "--max-chars", "1000").stdout
    )


def test_summarize_falls_back_and_engines(fake_api: str) -> None:
    from conftest import set_fake

    host = json.loads(
        run(fake_api, "--json", "summarize", "--episode-id", EP_READY, "--engine", "host").stdout
    )
    assert host["status"] == "transcript"
    assert not any("/artifacts/" in r["path"] for r in fake_requests(fake_api))
    set_fake(fake_api, llm_down=True)
    auto = run(fake_api, "summarize", "--episode-id", EP_READY, "--style", "verdict")
    assert auto.returncode == 0 and "server-side model is unavailable (llm_auth)" in auto.stdout
    assert "Who it's for" in auto.stdout and "(t=65)" in auto.stdout
    server = run(fake_api, "summarize", "--episode-id", EP_READY, "--engine", "server")
    assert server.returncode == 1 and "transcript --episode-id" in server.stdout
    ask = json.loads(run(fake_api, "--json", "ask", EP_READY, "Who is it for?").stdout)
    assert ask["status"] == "transcript" and "Who is it for?" in ask["task"]
    set_fake(fake_api, transcript_route=False)
    gap = run(fake_api, "summarize", "--episode-id", EP_READY)
    assert gap.returncode == 1 and "no transcript route yet" in gap.stderr
