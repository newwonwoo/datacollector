"""High-level workflows that string together collector primitives.

These are workflow-driven (cheap LLM at fixed steps), not agent-driven
(expensive LLM deciding everything). Designed so an external agent
(Claude Desktop / Cursor / AntiGravity via MCP) can call any single
workflow as one tool, getting a deterministic, reproducible result for
roughly $0.005–$0.05 per run on the cheap-model tier.

Public API:
    from collector.workflows import (
        brainstorm_topics, research_batch, synthesize, export_notebook,
    )

Submodules are named with leading underscore (`_batch`, `_synth`) to
avoid colliding with the public function names — that way attribute
access on the package returns the function, while tests can still
introspect the module via `collector.workflows._batch`.
"""
from __future__ import annotations

from .brainstorm import brainstorm_topics
from .export import export_notebook
from ._batch import research_batch
from ._synth import synthesize
from ._spec import design_spec

__all__ = [
    "brainstorm_topics", "research_batch", "synthesize",
    "design_spec", "export_notebook",
]
