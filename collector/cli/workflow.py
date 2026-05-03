"""`collector workflow` — high-level chains.

Subcommands:
    collector workflow brainstorm  --domain "..." --count 10 [--focus ...] [--exclude a,b]
    collector workflow research    --keywords-file ideas.json [--count 10] [--concurrency 3]
    collector workflow synthesize  --ideas-file ideas.json --research-file research.json
    collector workflow export      [--channel UC...] [--content-type concept] [--tag X]
    collector workflow full        --domain "..." --count 10  (brainstorm → research → synthesize → export)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..workflows import (
    brainstorm_topics,
    design_spec,
    export_notebook,
    research_batch,
    synthesize,
)


def _cmd_brainstorm(args: argparse.Namespace) -> int:
    excludes = [s.strip() for s in (args.exclude or "").split(",") if s.strip()]
    ideas = brainstorm_topics(
        domain=args.domain,
        count=args.count,
        focus=args.focus or "",
        exclude=excludes,
        keywords_per_idea=args.keywords_per_idea,
    )
    out = {"domain": args.domain, "count": len(ideas), "ideas": ideas}
    if args.out:
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {args.out}: {len(ideas)} ideas", file=sys.stderr)
    else:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def _load_notes(path: str | None) -> str | None:
    """Read user notes from a file (UTF-8). Returns None when path is
    empty/missing — callers treat None as 'no extra notes'."""
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        print(f"[notes] file not found: {path} — ignoring", file=sys.stderr)
        return None
    try:
        return p.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"[notes] failed to read {path}: {e}", file=sys.stderr)
        return None


def _flatten_keywords(ideas: list[dict]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in ideas:
        for kw in it.get("search_keywords") or []:
            kw = kw.strip()
            if kw and kw not in seen:
                seen.add(kw)
                out.append(kw)
    return out


def _cmd_research(args: argparse.Namespace) -> int:
    if args.keywords_file:
        body = json.loads(Path(args.keywords_file).read_text(encoding="utf-8"))
        if isinstance(body, dict) and "ideas" in body:
            keywords = _flatten_keywords(body["ideas"])
        elif isinstance(body, list):
            keywords = (
                _flatten_keywords(body)
                if body and isinstance(body[0], dict) and "search_keywords" in body[0]
                else [str(k) for k in body]
            )
        else:
            print("--keywords-file: shape not recognised", file=sys.stderr)
            return 2
    elif args.keywords:
        keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    else:
        print("provide --keywords-file or --keywords a,b,c", file=sys.stderr)
        return 2

    def _progress(kw: str, summary: dict) -> None:
        print(
            f"[research] {kw}: processed={summary.get('processed', 0)} "
            f"promoted={summary.get('promoted', 0)} "
            f"skipped={summary.get('skipped_duplicates', 0)}",
            file=sys.stderr,
        )

    results = research_batch(
        keywords,
        count_per_keyword=args.count,
        max_concurrency=args.concurrency,
        min_views=args.min_views,
        min_subscribers=args.min_subscribers,
        data_store_root=Path(args.data_store),
        logs_root=Path(args.logs),
        progress_cb=_progress,
    )
    out = {"keywords": len(keywords), "results": results}
    if args.out:
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {args.out}: {len(results)} results", file=sys.stderr)
    else:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def _cmd_synthesize(args: argparse.Namespace) -> int:
    ideas_body = json.loads(Path(args.ideas_file).read_text(encoding="utf-8"))
    ideas = ideas_body.get("ideas") if isinstance(ideas_body, dict) else ideas_body
    research_body = json.loads(Path(args.research_file).read_text(encoding="utf-8"))
    research = research_body.get("results") if isinstance(research_body, dict) else research_body
    out = synthesize(ideas, research)
    if args.out:
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def _cmd_design(args: argparse.Namespace) -> int:
    ideas_body = json.loads(Path(args.ideas_file).read_text(encoding="utf-8"))
    ideas = ideas_body.get("ideas") if isinstance(ideas_body, dict) else ideas_body
    research_body = json.loads(Path(args.research_file).read_text(encoding="utf-8"))
    research = research_body.get("results") if isinstance(research_body, dict) else research_body

    if args.synth_file:
        syn = json.loads(Path(args.synth_file).read_text(encoding="utf-8"))
        idx = syn.get("best_idea_index", 0)
    else:
        idx = args.best_index

    if not (0 <= idx < len(ideas)):
        print(f"best_idea_index {idx} out of range (0..{len(ideas)-1})", file=sys.stderr)
        return 2
    best = ideas[idx]

    vault_records: list = []
    ds = Path(args.data_store)
    if ds.exists():
        for p in ds.rglob("*.json"):
            try:
                vault_records.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue

    extra_notes = _load_notes(args.notes_file)
    out = design_spec(best, research, vault_records, extra_notes=extra_notes)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if args.json:
        out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        # Save the markdown body directly
        body = f"# {out['title']}\n\n{out['spec_md']}\n"
        out_path.write_text(body, encoding="utf-8")
    print(str(out_path))
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    path = export_notebook(
        data_store_root=Path(args.data_store),
        out_dir=Path(args.out_dir),
        channel_id=args.channel,
        content_type=args.content_type,
        tag=args.tag,
        only_promoted=not args.all_states,
        label=args.label or "",
    )
    print(str(path))
    return 0


def _cmd_full(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[full] step 1/4 brainstorm domain={args.domain} count={args.count}", file=sys.stderr)
    excludes = [s.strip() for s in (args.exclude or "").split(",") if s.strip()]
    ideas = brainstorm_topics(
        domain=args.domain, count=args.count, focus=args.focus or "",
        exclude=excludes, keywords_per_idea=args.keywords_per_idea,
    )
    (out_dir / "step1_ideas.json").write_text(
        json.dumps({"domain": args.domain, "ideas": ideas}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[full] {len(ideas)} ideas → step1_ideas.json", file=sys.stderr)

    keywords = _flatten_keywords(ideas)
    print(f"[full] step 2/4 research_batch keywords={len(keywords)}", file=sys.stderr)
    research = research_batch(
        keywords,
        count_per_keyword=args.videos_per_keyword,
        max_concurrency=args.concurrency,
        min_views=args.min_views,
        min_subscribers=args.min_subscribers,
        data_store_root=Path(args.data_store),
        logs_root=Path(args.logs),
        progress_cb=lambda kw, s: print(
            f"  [{kw}] processed={s.get('processed', 0)} promoted={s.get('promoted', 0)}",
            file=sys.stderr,
        ),
    )
    (out_dir / "step2_research.json").write_text(
        json.dumps({"results": research}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("[full] step 3/4 synthesize", file=sys.stderr)
    syn = synthesize(ideas, research)
    (out_dir / "step3_synthesize.json").write_text(
        json.dumps(syn, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    best_idx = syn.get("best_idea_index", -1)
    if 0 <= best_idx < len(ideas):
        print(f"[full] best idea = #{best_idx} '{ideas[best_idx]['idea']}'", file=sys.stderr)

    if 0 <= best_idx < len(ideas):
        print(f"[full] step 4/5 design_spec for best idea", file=sys.stderr)
        vault_records: list = []
        ds = Path(args.data_store)
        if ds.exists():
            for p in ds.rglob("*.json"):
                try:
                    vault_records.append(json.loads(p.read_text(encoding="utf-8")))
                except Exception:
                    continue
        try:
            extra_notes = _load_notes(args.notes_file)
            spec = design_spec(ideas[best_idx], research, vault_records,
                               extra_notes=extra_notes)
            spec_path = out_dir / f"step4_spec_{best_idx}.md"
            spec_path.write_text(
                f"# {spec['title']}\n\n{spec['spec_md']}\n", encoding="utf-8"
            )
            print(f"[full] design spec → {spec_path}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[full] design_spec failed (skipped): {e}", file=sys.stderr)

    print("[full] step 5/5 export NotebookLM bundle", file=sys.stderr)
    md_path = export_notebook(
        data_store_root=Path(args.data_store),
        out_dir=out_dir,
        only_promoted=True,
        label=args.domain.replace(" ", "_")[:30],
    )
    print(str(md_path))
    print(f"[full] done → {out_dir}/", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="collector workflow")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_b = sub.add_parser("brainstorm", help="ideas + youtube keywords (cheap LLM)")
    p_b.add_argument("--domain", required=True)
    p_b.add_argument("--count", type=int, default=10)
    p_b.add_argument("--focus", default="")
    p_b.add_argument("--exclude", default="", help="comma-separated")
    p_b.add_argument("--keywords-per-idea", type=int, default=3)
    p_b.add_argument("--out", default="")

    p_r = sub.add_parser("research", help="bounded-parallel run_query over keywords")
    p_r.add_argument("--keywords-file", default="")
    p_r.add_argument("--keywords", default="", help="comma-separated alternative")
    p_r.add_argument("--count", type=int, default=10)
    p_r.add_argument("--concurrency", type=int, default=None,
                     help="None = auto (1 if WARP, 3 otherwise)")
    p_r.add_argument("--min-views", type=int, default=0)
    p_r.add_argument("--min-subscribers", type=int, default=0)
    p_r.add_argument("--data-store", default="data_store")
    p_r.add_argument("--logs", default="logs")
    p_r.add_argument("--out", default="")

    p_s = sub.add_parser("synthesize", help="pick best idea (cheap LLM)")
    p_s.add_argument("--ideas-file", required=True)
    p_s.add_argument("--research-file", required=True)
    p_s.add_argument("--out", default="")

    p_d = sub.add_parser("design", help="generate a design spec markdown for the chosen idea")
    p_d.add_argument("--ideas-file", required=True)
    p_d.add_argument("--research-file", required=True)
    p_d.add_argument("--synth-file", default="",
                     help="step3_synthesize.json — picks best_idea_index from there")
    p_d.add_argument("--best-index", type=int, default=0,
                     help="alternative to --synth-file: directly pick by index")
    p_d.add_argument("--data-store", default="data_store")
    p_d.add_argument("--notes-file", default="",
                     help="추가 반영할 텍스트 파일 (NotebookLM 브리프·도메인 메모 등). "
                          "8k자까지 user_notes 로 LLM 입력에 합쳐짐.")
    p_d.add_argument("--out", default="exports/spec.md")
    p_d.add_argument("--json", action="store_true",
                     help="dump the raw {title, spec_md} JSON instead of pure markdown")

    p_e = sub.add_parser("export", help="combine vault notes → single .md for NotebookLM")
    p_e.add_argument("--data-store", default="data_store")
    p_e.add_argument("--out-dir", default="exports")
    p_e.add_argument("--channel", default=None)
    p_e.add_argument("--content-type", default=None)
    p_e.add_argument("--tag", default=None)
    p_e.add_argument("--all-states", action="store_true",
                     help="포함: 미promoted 까지. 기본은 promoted 만")
    p_e.add_argument("--label", default="")

    p_f = sub.add_parser("full", help="brainstorm → research → synthesize → export 한 번에")
    p_f.add_argument("--domain", required=True)
    p_f.add_argument("--count", type=int, default=10)
    p_f.add_argument("--keywords-per-idea", type=int, default=3)
    p_f.add_argument("--videos-per-keyword", type=int, default=10)
    p_f.add_argument("--focus", default="")
    p_f.add_argument("--exclude", default="")
    p_f.add_argument("--concurrency", type=int, default=None)
    p_f.add_argument("--min-views", type=int, default=0)
    p_f.add_argument("--min-subscribers", type=int, default=0)
    p_f.add_argument("--data-store", default="data_store")
    p_f.add_argument("--logs", default="logs")
    p_f.add_argument("--notes-file", default="",
                     help="step 4(design_spec) 에 추가 반영할 텍스트 파일")
    p_f.add_argument("--out-dir", default="exports/run")

    args = ap.parse_args(argv)
    return {
        "brainstorm": _cmd_brainstorm,
        "research": _cmd_research,
        "synthesize": _cmd_synthesize,
        "design": _cmd_design,
        "export": _cmd_export,
        "full": _cmd_full,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
