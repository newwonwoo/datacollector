# fablize (vendored)

This directory vendors the verified procedures from
[**fivetaku/fablize**](https://github.com/fivetaku/fablize) (MIT) directly into
this repository, so every Claude Code session — including web/remote sessions
where the plugin marketplace is not installed — has them resident.

> fablize makes Claude (Opus or any model) **see a task through to the end —
> with evidence and verification — as procedure, not as luck.** It does not
> raise the model's ceiling; it makes the model reach its own ceiling. At the
> capability ceiling it tells you to escalate instead of pretending.

## How it is wired here

- **Operating block** — appended to the repo-root `CLAUDE.md` between
  `<!-- FABLIZE:BEGIN -->` / `<!-- FABLIZE:END -->` markers. These are the
  always-on routing rules every session reads.
- **`.claude/settings.json`** — registers two hooks:
  - `UserPromptSubmit` → `hooks/router.sh` injects the matching pack
    (investigation / verification-grounding) when a task signal is detected.
  - `Stop` → `hooks/finish-the-work.sh` blocks an early stop that only
    *promises* work without doing it.
- **`scripts/goals.py`** — the multi-story loop + verification gate
  (stdlib-only). Run it from the repo root; runtime state lives in
  `./.fablize/` (gitignored).
- **`packs/`** — the investigation protocol and verification-grounding
  disciplines, read on demand.

## Update / remove

Re-vendor by copying `scripts/`, `packs/`, and `hooks/` from the upstream repo.
To remove fablize: delete the `<!-- FABLIZE:BEGIN … END -->` block from
`CLAUDE.md`, remove the two hooks from `.claude/settings.json`, and delete this
directory.

Upstream: https://github.com/fivetaku/fablize · License: MIT
