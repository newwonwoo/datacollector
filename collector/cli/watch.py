"""`collector watch` — recurring research + NotebookLM refresh.

A registered domain has keywords + modes + an interval. On each tick:
  1. research_batch over the keywords (vault dedup makes it free for
     videos we've already seen).
  2. Diff the vault snapshot vs the last tick → {new_videos, promoted,
     invalid}.
  3. If anything new, build the raw bundle and replace the
     notebook's source so NotebookLM is reading the freshest cut.
  4. Persist the result in the registry.

Modes:
  --once       : single tick, exit. Designed for cron / Task Scheduler.
  --interval   : daemon-mode sleep loop (daily/weekly/manual). Manual
                 means register only — no automatic ticks.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Iterable

from .. import watch_registry as reg
from ..workflows import research_batch
from ..workflows.export import export_raw_bundle


_INTERVAL_SECONDS = {
    "daily":   86400,
    "weekly":  604800,
    "monthly": 2592000,   # 30d ≈ for "monthly enough" semantics
    "manual":  0,         # 0 == never auto-tick
}


def _parse_csv(s: str) -> list[str]:
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def _vault_snapshot(data_store: Path) -> dict[str, str]:
    """Map source_key → record_status. Cheap to recompute every tick."""
    out: dict[str, str] = {}
    if not data_store.exists():
        return out
    for p in data_store.rglob("*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        sk = rec.get("source_key") or rec.get("video_id")
        if sk:
            out[str(sk)] = str(rec.get("record_status") or "")
    return out


def _diff(prev: dict[str, str], cur: dict[str, str]) -> dict[str, int]:
    new_keys = set(cur) - set(prev)
    return {
        "new_videos": len(new_keys),
        "promoted":   sum(1 for k in new_keys if cur.get(k) == "promoted"),
        "invalid":    sum(1 for k in new_keys if cur.get(k) == "invalid"),
    }


def run_tick(
    domain: str,
    *,
    keywords: list[str] | None = None,
    modes: list[str] | None = None,
    channels: list[str] | None = None,
    videos_per_keyword: int = 10,
    data_store_root: Path = Path("data_store"),
    logs_root: Path = Path("logs"),
    registry_path: Path = reg.DEFAULT_PATH,
    refresh_notebook: bool = True,
    out_dir: Path = Path("exports/watch"),
) -> dict:
    """Execute a single tick. Returns the diff + status."""
    items = reg.load(registry_path)
    entry = items.get(domain) or {}
    kw = keywords or entry.get("keywords") or []
    md = modes or entry.get("modes") or []
    chs = channels or entry.get("channels") or []
    if not kw and not chs:
        raise ValueError(f"watch[{domain}]: no keywords nor channels registered")

    prev_snap = entry.get("vault_snapshot") or {}
    sys.stderr.write(
        f"[watch] {domain}: research_batch over {len(kw)} keyword(s) × "
        f"{len(chs) or 1} channel(s)\n"
    )
    research_batch(
        kw,
        count_per_keyword=videos_per_keyword,
        data_store_root=data_store_root,
        logs_root=logs_root,
        target_channel_ids=chs or None,
    )

    cur_snap = _vault_snapshot(data_store_root)
    diff = _diff(prev_snap, cur_snap)
    sys.stderr.write(
        f"[watch] {domain}: +{diff['new_videos']} new "
        f"({diff['promoted']} promoted, {diff['invalid']} invalid)\n"
    )

    notebook_id = entry.get("notebook_id")
    nb_error: str | None = None
    if refresh_notebook and diff["new_videos"] > 0 and md:
        try:
            from ..adapters import notebooklm
            from ..workflows._nb_specs import generate_specs
            bundle = export_raw_bundle(
                data_store_root=data_store_root,
                out_dir=out_dir / domain,
                only_promoted=True,
                label=domain,
            )
            if not notebook_id:
                from datetime import datetime, timezone
                title = f"{domain}_collector_{datetime.now(timezone.utc).strftime('%Y%m%d')}"
                notebook_id = notebooklm.create_notebook(title)
                notebooklm.add_source(notebook_id, bundle)
            else:
                notebooklm.replace_source(notebook_id, bundle)
            generate_specs(
                domain=domain, bundle_path=bundle, modes=md,
                out_dir=out_dir / domain,
            )
        except Exception as e:  # noqa: BLE001
            nb_error = f"{type(e).__name__}: {e}"
            sys.stderr.write(f"[watch] {domain}: notebook refresh failed: {nb_error}\n")

    # Persist updated state regardless of NotebookLM outcome.
    reg.upsert(domain, path=registry_path,
               keywords=kw, modes=md,
               vault_snapshot=cur_snap)
    reg.record_run(
        domain, path=registry_path,
        new_videos=diff["new_videos"],
        promoted=diff["promoted"],
        invalid=diff["invalid"],
        status="failed" if nb_error else "completed",
        error=nb_error,
        notebook_id=notebook_id,
    )
    return {"domain": domain, "diff": diff,
            "notebook_id": notebook_id, "error": nb_error}


def cmd_register(args) -> int:
    kw = _parse_csv(args.keywords)
    md = _parse_csv(args.modes)
    raw_channels = _parse_csv(args.channels)
    interval = args.interval or "manual"
    if interval not in _INTERVAL_SECONDS:
        print(f"--interval must be one of {list(_INTERVAL_SECONDS)}", file=sys.stderr)
        return 2

    # Resolve channels via the YouTube adapter (UC… passthrough, others
    # via channels.list). We resolve at register time so each tick is
    # cheap (no API call to convert handles repeatedly).
    resolved: list[str] = []
    if raw_channels:
        import os
        api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
        for raw in raw_channels:
            if raw.startswith("UC") and len(raw) >= 22 and " " not in raw:
                resolved.append(raw[:24])
                continue
            if not api_key:
                print(f"--channels: '{raw}' 변환 불가 (YOUTUBE_API_KEY 필요)",
                      file=sys.stderr)
                return 2
            from ..adapters.youtube import YouTubeAdapter
            cid = YouTubeAdapter(api_key).resolve_channel(raw)
            if not cid:
                print(f"--channels: '{raw}' 인식 불가", file=sys.stderr)
                return 2
            resolved.append(cid)

    if not kw and not resolved:
        print("--keywords 또는 --channels 둘 중 하나는 필요합니다", file=sys.stderr)
        return 2

    reg.upsert(
        args.domain, path=Path(args.registry),
        keywords=kw, modes=md,
        channels=resolved, channel_inputs=raw_channels,
        interval=interval,
        videos_per_keyword=args.videos_per_keyword,
        paused=False,
    )
    print(f"watch registered: {args.domain} ({len(kw)} keywords, "
          f"{len(resolved)} channels, interval={interval})")
    return 0


def cmd_list(args) -> int:
    items = reg.load(Path(args.registry))
    if not items:
        print("(no watches)")
        return 0
    for domain, e in items.items():
        flag = " [PAUSED]" if e.get("paused") else ""
        last = e.get("last_run") or "—"
        d = e.get("last_diff") or {}
        diff = (f"+{d.get('new_videos', 0)} (✓{d.get('promoted', 0)}, "
                f"✗{d.get('invalid', 0)})") if d else "—"
        print(f"{domain}{flag}  interval={e.get('interval', '?')}  "
              f"last={last}  Δ={diff}")
    return 0


def cmd_remove(args) -> int:
    if reg.remove(args.domain, Path(args.registry)):
        print(f"removed: {args.domain}")
        return 0
    print(f"not found: {args.domain}", file=sys.stderr)
    return 1


def cmd_run(args) -> int:
    """Execute one or more ticks. --once = 1 tick. Otherwise loop on the
    domain's registered interval (or args.loop_seconds for testing)."""
    domain = args.domain
    items = reg.load(Path(args.registry))
    entry = items.get(domain)
    if entry is None:
        print(f"watch not registered: {domain}", file=sys.stderr)
        return 2
    if entry.get("paused"):
        print(f"watch paused: {domain}", file=sys.stderr)
        return 0

    def _tick() -> int:
        try:
            run_tick(
                domain,
                keywords=entry.get("keywords"),
                modes=entry.get("modes"),
                videos_per_keyword=entry.get("videos_per_keyword", 10),
                data_store_root=Path(args.data_store),
                logs_root=Path(args.logs),
                registry_path=Path(args.registry),
                refresh_notebook=not args.no_notebook,
                out_dir=Path(args.out_dir),
            )
            return 0
        except Exception as e:  # noqa: BLE001
            print(f"[watch] {domain} tick failed: {e}", file=sys.stderr)
            reg.record_run(domain, path=Path(args.registry),
                           status="failed", error=f"{type(e).__name__}: {e}")
            return 1

    if args.once:
        return _tick()

    interval_sec = (args.loop_seconds
                    or _INTERVAL_SECONDS.get(entry.get("interval", "manual"), 0))
    if interval_sec <= 0:
        print(f"watch {domain}: interval=manual, run once and exiting",
              file=sys.stderr)
        return _tick()

    print(f"[watch] {domain}: looping every {interval_sec}s (Ctrl+C to stop)",
          file=sys.stderr)
    while True:
        rc = _tick()
        if rc != 0:
            print(f"[watch] {domain}: tick rc={rc}, continuing", file=sys.stderr)
        time.sleep(interval_sec)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="collector watch")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_reg = sub.add_parser("register", help="새 watch 등록 (또는 갱신)")
    p_reg.add_argument("--domain", required=True)
    p_reg.add_argument("--keywords", default="",
                       help="comma-separated. --channels 와 함께 또는 따로 지정 가능 "
                            "(둘 중 하나는 필수)")
    p_reg.add_argument("--channels", default="",
                       help="comma-separated. URL/@핸들/UC… 어떤 형식이든 OK. "
                            "여러 개 지정 시 각 채널별로 별도 sub-run 으로 실행")
    p_reg.add_argument("--modes", default="",
                       help="comma-separated subset of monetize,textbook,summary,presentation")
    p_reg.add_argument("--interval", default="manual",
                       choices=list(_INTERVAL_SECONDS))
    p_reg.add_argument("--videos-per-keyword", type=int, default=10)
    p_reg.add_argument("--registry", default=str(reg.DEFAULT_PATH))

    p_list = sub.add_parser("list", help="등록된 watch 목록")
    p_list.add_argument("--registry", default=str(reg.DEFAULT_PATH))

    p_rm = sub.add_parser("remove", help="watch 삭제")
    p_rm.add_argument("--domain", required=True)
    p_rm.add_argument("--registry", default=str(reg.DEFAULT_PATH))

    p_run = sub.add_parser("run", help="한 tick 또는 daemon 실행")
    p_run.add_argument("--domain", required=True)
    p_run.add_argument("--once", action="store_true",
                       help="1회 실행 후 종료 (cron/Task Scheduler 호환)")
    p_run.add_argument("--no-notebook", action="store_true",
                       help="NotebookLM 호출 없이 vault 만 갱신")
    p_run.add_argument("--loop-seconds", type=int, default=0,
                       help="(테스트용) interval 무시하고 N초 주기로 루프")
    p_run.add_argument("--data-store", default="data_store")
    p_run.add_argument("--logs", default="logs")
    p_run.add_argument("--out-dir", default="exports/watch")
    p_run.add_argument("--registry", default=str(reg.DEFAULT_PATH))

    args = ap.parse_args(argv)
    return {
        "register": cmd_register,
        "list":     cmd_list,
        "remove":   cmd_remove,
        "run":      cmd_run,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
