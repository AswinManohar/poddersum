"""OpenAPI generation with first-class SSE documentation.

FastAPI only knows JSON bodies. SSE routes declare ``openapi_extra=sse_doc(...)``; this
module then documents their 200 response as ``text/event-stream`` whose frames carry the
named schema, and makes sure SSE-only models (``JobEvent``, ``AnswerStreamEvent`` and the
event models) exist in ``components.schemas`` so ``frontend/lib/types.ts`` gets them.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from pydantic import TypeAdapter

from api.schemas import CONTRACT_MODELS, AnswerStreamEvent, JobEvent, VerdictSection

SSE_KEY = "x-sse"
REF = "#/components/schemas/{model}"

# Named stream payload schemas: component name -> Python type.
STREAM_SCHEMAS: dict[str, Any] = {
    "JobEvent": JobEvent,
    "AnswerStreamEvent": AnswerStreamEvent,
}
# Types no route references directly but the frontend needs.
EXTRA_SCHEMAS: dict[str, Any] = {"VerdictSection": VerdictSection}


def sse_doc(schema: str, events: list[str], description: str) -> dict[str, Any]:
    """``openapi_extra`` for an SSE route whose frames' ``data`` is component ``schema``."""
    if schema not in STREAM_SCHEMAS:
        raise ValueError(f"unknown stream schema {schema}")
    return {SSE_KEY: {"schema": schema, "events": events, "description": description}}


def _add_component(components: dict[str, Any], name: str, tp: Any) -> None:
    js = TypeAdapter(tp).json_schema(ref_template=REF, mode="serialization")
    for def_name, def_schema in js.pop("$defs", {}).items():
        components.setdefault(def_name, def_schema)
    js.setdefault("title", name)
    components.setdefault(name, js)


def _fix_sse_operation(op: dict[str, Any]) -> None:
    meta = op.get(SSE_KEY)
    if not meta:
        return
    responses = op.setdefault("responses", {})
    responses["200"] = {
        "description": meta["description"],
        "content": {"text/event-stream": {"schema": {"$ref": REF.format(model=meta["schema"])}}},
    }
    # Errors are JSON even on streaming routes (they happen before the stream starts).
    for status, resp in responses.items():
        content = resp.get("content")
        if status != "200" and content and "text/event-stream" in content:
            content.setdefault("application/json", content.pop("text/event-stream"))


def build_openapi(app: FastAPI) -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    for model in CONTRACT_MODELS:
        _add_component(components, model.__name__, model)
    for name, tp in {**STREAM_SCHEMAS, **EXTRA_SCHEMAS}.items():
        _add_component(components, name, tp)
    for path_item in schema.get("paths", {}).values():
        for op in path_item.values():
            if isinstance(op, dict):
                _fix_sse_operation(op)
    app.openapi_schema = schema
    return schema


def install(app: FastAPI) -> None:
    app.openapi = lambda: build_openapi(app)  # type: ignore[method-assign]
