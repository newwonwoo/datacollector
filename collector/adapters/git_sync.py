"""Git Sync adapter via git CLI + GitHub App installation token."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Callable

from ..services import MockError


def _default_run(cmd: list[str], *, cwd: str | None = None, env: dict | None = None, check: bool = True) -> dict:
    r = subprocess.run(cmd, cwd=cwd, env=env or os.environ.copy(), check=False, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise MockError("GIT_CONFLICT" if "conflict" in r.stderr.lower() else "GIT_ERROR", r.stderr[:400])
    return {"code": r.returncode, "stdout": r.stdout, "stderr": r.stderr}


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _sign_jwt_rs256(header: dict, claims: dict, private_key_pem: str, signer: Callable | None = None) -> str:
    """Sign a JWT (RS256). If `signer` is None, attempts lazy import of cryptography.
    `signer(data: bytes, pem: str) -> bytes` for test injection.
    """
    h = _b64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
    c = _b64url(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    message = f"{h}.{c}".encode()
    if signer is None:
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding

            key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
            sig = key.sign(message, padding.PKCS1v15(), hashes.SHA256())
        except Exception as e:
            raise MockError("GIT_AUTH", f"jwt sign failed: {e}")
    else:
        sig = signer(message, private_key_pem)
    return f"{h}.{c}.{_b64url(sig)}"


class GitSyncAdapter:
    API = "https://api.github.com"

    def __init__(
        self,
        *,
        app_id: str,
        installation_id: str,
        private_key_pem: str,
        repo: str,  # "owner/name"
        branch: str = "data/main",
        work_root: str | Path = "/tmp/git-sync",
        run: Callable = _default_run,
        http: Callable | None = None,
        signer: Callable | None = None,
    ):
        self.app_id = app_id
        self.installation_id = installation_id
        self.private_key_pem = private_key_pem
        self.repo = repo
        self.branch = branch
        self.work_root = Path(work_root)
        self.run = run
        self.signer = signer
        self.http = http or self._default_http

    def sync(self, payload: dict[str, Any]) -> None:
        token = self._installation_token()
        work = self.work_root / self.repo.replace("/", "__")
        work.mkdir(parents=True, exist_ok=True)
        repo_url = f"https://x-access-token:{token}@github.com/{self.repo}.git"
        if not (work / ".git").exists():
            self.run(["git", "clone", "--branch", self.branch, repo_url, str(work)])
        else:
            self.run(["git", "-C", str(work), "fetch", "origin", self.branch])
            self.run(["git", "-C", str(work), "reset", "--hard", f"origin/{self.branch}"])
        # write markdown
        md_dir = work / "notes"
        md_dir.mkdir(parents=True, exist_ok=True)
        md_path = md_dir / f"{payload['source_key'].replace(':', '__')}.md"
        md_path.write_text(self._render_markdown(payload), encoding="utf-8")
        self.run(["git", "-C", str(work), "add", str(md_path.relative_to(work))])
        self.run([
            "git", "-C", str(work), "commit", "-m",
            f"data: {payload['source_key']} v{payload.get('payload_version', 1)}",
        ])
        self.run(["git", "-C", str(work), "push", "origin", self.branch])

    # ---------- helpers ----------

    def _installation_token(self) -> str:
        now = int(time.time())
        jwt = _sign_jwt_rs256(
            {"alg": "RS256", "typ": "JWT"},
            {"iat": now - 30, "exp": now + 540, "iss": self.app_id},
            self.private_key_pem,
            signer=self.signer,
        )
        req = urllib.request.Request(
            f"{self.API}/app/installations/{self.installation_id}/access_tokens",
            method="POST",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {jwt}",
                "User-Agent": "collector-git-sync",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())["token"]
        except urllib.error.HTTPError as e:
            raise MockError("GIT_AUTH", f"installation token: {e.code}: {e.read().decode('utf-8','replace')[:200]}")

    @staticmethod
    def _default_http(method, url, *, headers=None, data=None):
        raise NotImplementedError

    @staticmethod
    def _render_markdown(payload: dict[str, Any]) -> str:
        lines = [
            "---",
            f"source_key: {payload.get('source_key')}",
            f"video_id: {payload.get('video_id')}",
            f"title: {payload.get('title', '')}",
            f"published_at: {payload.get('published_at', '')}",
            f"collected_at: {payload.get('collected_at', '')}",
            f"confidence: {payload.get('confidence', '')}",
            f"schema_version: {payload.get('schema_version', '')}",
            "tags: [" + ", ".join(payload.get("tags", [])) + "]",
            "---",
            "",
            "## Summary",
            payload.get("summary", ""),
            "",
            "## Rules",
        ]
        for r in payload.get("rules", []):
            lines.append(f"- {r}")
        lines += ["", f"원본: https://www.youtube.com/watch?v={payload.get('video_id','')}", ""]
        return "\n".join(lines)
