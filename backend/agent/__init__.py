"""PodderSum answer engine: grounded, cited answers from stored transcript segments.

Entry points:

* ``agent.answer_graph.stream_answer`` - ask anything, streamed as contract SSE events.
* ``agent.presets.get_or_create_artifact`` - TL;DR, takeaways and verdict, cached.

Both read stored text segments only (never audio), send the whole transcript as a stable
prompt prefix, and return only claims whose citations were checked against the database
(``agent.citations``). Prompts live in ``agent/prompts/`` with explicit versions.
"""
