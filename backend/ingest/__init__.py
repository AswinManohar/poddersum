"""Ingest pipeline: transcript (feed tag or audio STT) -> segments -> Postgres.

Entry point: :func:`ingest.runner.enqueue_ingest`. See ``ingest/runner.py`` for the job
lifecycle and the step functions.
"""
