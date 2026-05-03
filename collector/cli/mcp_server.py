"""Minimal MCP (Model Context Protocol) stdio server.

Exposes collector primitives so any MCP-aware client (Claude Desktop,
Cursor, Codex CLI, AntiGravity, etc.) can call them autonomously.

We implement a small subset of MCP rather than depending on the
external `mcp` SDK so `collector mcp` works on a clean Python install.
The server speaks JSON-RPC 2.0 over stdin/stdout, line-delimited.

Tools exposed:
    run_query          — run the YouTube pipeline on a query
    search_notes       — full-text search across vault
    get_note           — fetch a single payload by source_key
    list_recent        — most-recent records (vault + meta)
    list_channels      — channel breakdown with note counts
    get_pipeline_status — current /api/run state
    brainstorm_topics  — workflows.brainstorm
    research_batch     — workflows.research_batch
    synthesize         — workflows.synthesize
    export_notebook    — workflows.export

Resources:
    vault://strategies/{source_key} — markdown note body

Client setup example (Claude Desktop ~/.claude/claude_desktop_config.json):

    {
      "mcpServers": {
        "collector": {
          "command": "python",
          "args": ["-m", "collector", "mcp"],
          "env": {"COLLECTOR_DATA_STORE": "C:/Users/.../datacollector/data_store"}
        }
      }
    }
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any


_PROTOCOL_VERSION = "2024-11-05"
_SERVER_NAME = "collector"
_SERVER_VERSION = "10.1.0"


def _emit(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(req_id: Any, result: Any) -> None:
    _emit({"jsonrpc": "2.0", "id": req_id, "result": result})


def _error(req_id: Any, code: int, message: str, data: Any = None) -> None:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    _emit({"jsonrpc": "2.0", "id": req_id, "error": err})


# ---------- Tool implementations ----------

def _data_store() -> Path:
    return Path(os.environ.get("COLLECTOR_DATA_STORE", "data_store"))


def _logs_root() -> Path:
    return Path(os.environ.get("COLLECTOR_LOGS", "logs"))


def _vault_root() -> Path:
    return Path(os.environ.get("COLLECTOR_VAULT", "vault"))


def _exports_root() -> Path:
    return Path(os.environ.get("COLLECTOR_EXPORTS", "exports"))


def _read_payloads() -> list[dict]:
    out: list[dict] = []
    ds = _data_store()
    if not ds.exists():
        return out
    for p in ds.rglob("*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            rec.pop("transcript", None)
            out.append(rec)
        except Exception:  # noqa: BLE001
            continue
    out.sort(key=lambda r: (r.get("collected_at") or ""), reverse=True)
    return out


def tool_run_query(args: dict) -> dict:
    from .run import run_query
    return run_query(
        args["query"],
        count=int(args.get("count", 10)),
        data_store_root=_data_store(),
        logs_root=_logs_root(),
        llm_choice=args.get("llm_choice"),
        target_channel_id=args.get("target_channel_id"),
        min_views=int(args.get("min_views", 0)),
        min_subscribers=int(args.get("min_subscribers", 0)),
    )


def tool_search_notes(args: dict) -> dict:
    q = (args.get("query") or "").strip().lower()
    if not q:
        return {"matches": [], "total": 0}
    limit = int(args.get("limit", 20))
    results = []
    for rec in _read_payloads():
        haystacks = " ".join([
            rec.get("title", "") or "",
            rec.get("summary", "") or "",
            " ".join(rec.get("rules") or []),
            " ".join(rec.get("knowledge") or []),
            " ".join(rec.get("examples") or []),
            " ".join(rec.get("claims") or []),
            " ".join(rec.get("tags") or []),
            rec.get("notes_md", "") or "",
        ]).lower()
        if q in haystacks:
            results.append({
                "source_key": rec.get("source_key"),
                "title": rec.get("title"),
                "summary": rec.get("summary"),
                "record_status": rec.get("record_status"),
                "content_type": rec.get("content_type"),
                "channel_id": rec.get("channel_id"),
                "tags": rec.get("tags"),
            })
            if len(results) >= limit:
                break
    return {"matches": results, "total": len(results)}


def tool_get_note(args: dict) -> dict:
    sk = (args.get("source_key") or "").strip()
    if not sk:
        raise ValueError("source_key required")
    for rec in _read_payloads():
        if rec.get("source_key") == sk:
            return rec
    raise ValueError(f"note not found: {sk}")


def tool_list_recent(args: dict) -> dict:
    limit = int(args.get("limit", 20))
    return {"records": _read_payloads()[:limit]}


def tool_list_channels(args: dict) -> dict:
    from collections import Counter
    by_ch: dict[str, dict[str, Any]] = {}
    promoted: Counter = Counter()
    for rec in _read_payloads():
        ch = rec.get("channel_id") or "_unknown"
        by_ch.setdefault(ch, {"channel_id": ch, "count": 0, "promoted": 0, "tags": Counter()})
        by_ch[ch]["count"] += 1
        if rec.get("record_status") == "promoted":
            by_ch[ch]["promoted"] += 1
        for t in rec.get("tags") or []:
            by_ch[ch]["tags"][t] += 1
    out = []
    for ch, info in sorted(by_ch.items(), key=lambda kv: -kv[1]["count"]):
        out.append({
            "channel_id": info["channel_id"],
            "count": info["count"],
            "promoted": info["promoted"],
            "top_tags": [t for t, _ in info["tags"].most_common(5)],
        })
    return {"channels": out}


def tool_get_pipeline_status(args: dict) -> dict:
    try:
        from .api_handler import _RUN_STATE, _RUN_LOCK
        with _RUN_LOCK:
            return dict(_RUN_STATE)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def tool_brainstorm_topics(args: dict) -> dict:
    from ..workflows import brainstorm_topics
    excludes = args.get("exclude") or []
    if isinstance(excludes, str):
        excludes = [s.strip() for s in excludes.split(",") if s.strip()]
    ideas = brainstorm_topics(
        domain=args["domain"],
        count=int(args.get("count", 10)),
        focus=args.get("focus", "") or "",
        exclude=excludes,
        keywords_per_idea=int(args.get("keywords_per_idea", 3)),
    )
    return {"ideas": ideas}


def tool_research_batch(args: dict) -> dict:
    from ..workflows import research_batch
    keywords = args["keywords"]
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]
    results = research_batch(
        keywords,
        count_per_keyword=int(args.get("count_per_keyword", 10)),
        max_concurrency=args.get("max_concurrency"),
        min_views=int(args.get("min_views", 0)),
        min_subscribers=int(args.get("min_subscribers", 0)),
        data_store_root=_data_store(),
        logs_root=_logs_root(),
    )
    return {"results": results}


def tool_synthesize(args: dict) -> dict:
    from ..workflows import synthesize
    return synthesize(args["ideas"], args["research_results"])


def tool_export_notebook(args: dict) -> dict:
    from ..workflows import export_notebook
    path = export_notebook(
        data_store_root=_data_store(),
        out_dir=_exports_root(),
        channel_id=args.get("channel_id"),
        content_type=args.get("content_type"),
        tag=args.get("tag"),
        only_promoted=bool(args.get("only_promoted", True)),
        label=args.get("label", "") or "",
    )
    return {"path": str(path)}


_TOOLS: dict[str, dict[str, Any]] = {
    "run_query": {
        "fn": tool_run_query,
        "description": "Search YouTube + fetch captions + LLM extract + write to vault. Returns run summary.",
        "schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색어 (한국어/영어)"},
                "count": {"type": "integer", "default": 10, "minimum": 1, "maximum": 200},
                "min_views": {"type": "integer", "default": 0},
                "min_subscribers": {"type": "integer", "default": 0},
                "llm_choice": {"type": "string", "enum": ["gemini", "groq", "anthropic"]},
                "target_channel_id": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    "search_notes": {
        "fn": tool_search_notes,
        "description": "Substring search across all vault notes (title/summary/rules/knowledge/tags/notes_md).",
        "schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
    },
    "get_note": {
        "fn": tool_get_note,
        "description": "Full payload (summary/rules/knowledge/notes_md/...) for a single source_key.",
        "schema": {
            "type": "object",
            "properties": {"source_key": {"type": "string"}},
            "required": ["source_key"],
        },
    },
    "list_recent": {
        "fn": tool_list_recent,
        "description": "Most recently collected records (newest first), without raw transcripts.",
        "schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 20}},
        },
    },
    "list_channels": {
        "fn": tool_list_channels,
        "description": "Channel breakdown — counts of total / promoted records and top tags per channel.",
        "schema": {"type": "object", "properties": {}},
    },
    "get_pipeline_status": {
        "fn": tool_get_pipeline_status,
        "description": "Current local-mode pipeline run state (idle/running/completed/failed) + last summary.",
        "schema": {"type": "object", "properties": {}},
    },
    "brainstorm_topics": {
        "fn": tool_brainstorm_topics,
        "description": "Generate N business ideas in a domain, each with 2-3 YouTube search keywords. Single cheap-LLM call.",
        "schema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string"},
                "count": {"type": "integer", "default": 10},
                "focus": {"type": "string"},
                "exclude": {"type": "array", "items": {"type": "string"}},
                "keywords_per_idea": {"type": "integer", "default": 3},
            },
            "required": ["domain"],
        },
    },
    "research_batch": {
        "fn": tool_research_batch,
        "description": "Run run_query for many keywords with bounded concurrency (WARP-aware default).",
        "schema": {
            "type": "object",
            "properties": {
                "keywords": {"type": "array", "items": {"type": "string"}},
                "count_per_keyword": {"type": "integer", "default": 10},
                "max_concurrency": {"type": ["integer", "null"]},
                "min_views": {"type": "integer", "default": 0},
                "min_subscribers": {"type": "integer", "default": 0},
            },
            "required": ["keywords"],
        },
    },
    "synthesize": {
        "fn": tool_synthesize,
        "description": "Score ideas based on research_batch output and pick best one. Single cheap-LLM call.",
        "schema": {
            "type": "object",
            "properties": {
                "ideas": {"type": "array"},
                "research_results": {"type": "array"},
            },
            "required": ["ideas", "research_results"],
        },
    },
    "export_notebook": {
        "fn": tool_export_notebook,
        "description": "Bundle filtered vault notes into one Markdown file ready for NotebookLM upload. Returns path.",
        "schema": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"},
                "content_type": {"type": "string"},
                "tag": {"type": "string"},
                "only_promoted": {"type": "boolean", "default": True},
                "label": {"type": "string"},
            },
        },
    },
}


# ---------- Resources (vault://) ----------

def _resource_uris() -> list[dict]:
    out = []
    vault = _vault_root() / "strategies"
    if vault.exists():
        for p in sorted(vault.glob("*.md")):
            out.append({
                "uri": f"vault://strategies/{p.stem}",
                "name": p.stem,
                "mimeType": "text/markdown",
            })
    return out


def _resource_read(uri: str) -> dict:
    if not uri.startswith("vault://strategies/"):
        raise ValueError(f"unknown URI scheme: {uri}")
    name = uri[len("vault://strategies/"):]
    safe = name.replace("/", "_").replace("..", "")
    p = _vault_root() / "strategies" / f"{safe}.md"
    if not p.exists():
        raise ValueError(f"resource not found: {uri}")
    return {
        "contents": [{
            "uri": uri,
            "mimeType": "text/markdown",
            "text": p.read_text(encoding="utf-8"),
        }],
    }


# ---------- JSON-RPC dispatch ----------

def _handle(method: str, params: dict) -> Any:
    if method == "initialize":
        return {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {"tools": {}, "resources": {}},
            "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
        }
    if method == "initialized" or method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {
            "tools": [
                {
                    "name": name,
                    "description": meta["description"],
                    "inputSchema": meta["schema"],
                }
                for name, meta in _TOOLS.items()
            ],
        }
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        meta = _TOOLS.get(name)
        if meta is None:
            raise ValueError(f"unknown tool: {name}")
        result = meta["fn"](args)
        return {
            "content": [
                {"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)},
            ],
        }
    if method == "resources/list":
        return {"resources": _resource_uris()}
    if method == "resources/read":
        return _resource_read(params.get("uri", ""))
    if method == "ping":
        return {}
    raise ValueError(f"unsupported method: {method}")


def serve() -> int:
    sys.stderr.write(
        f"[mcp] {_SERVER_NAME} v{_SERVER_VERSION} stdio — "
        f"{len(_TOOLS)} tools, vault://strategies/* resources\n"
    )
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            _emit({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"parse error: {e}"}})
            continue
        req_id = req.get("id")
        method = req.get("method", "")
        params = req.get("params") or {}
        try:
            result = _handle(method, params)
            if result is None and req_id is None:
                # notification — no response
                continue
            _result(req_id, result)
        except ValueError as e:
            _error(req_id, -32601, str(e))
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[mcp] error in {method}: {e}\n{traceback.format_exc()}")
            _error(req_id, -32603, f"{type(e).__name__}: {e}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="collector mcp",
                                  description="MCP stdio server exposing collector tools.")
    ap.add_argument("--list-tools", action="store_true",
                    help="print tool schemas as JSON and exit (debugging)")
    args = ap.parse_args(argv)
    if args.list_tools:
        print(json.dumps(
            {"tools": [{"name": n, "description": m["description"], "inputSchema": m["schema"]}
                       for n, m in _TOOLS.items()]},
            ensure_ascii=False, indent=2))
        return 0
    return serve()


if __name__ == "__main__":
    sys.exit(main())
