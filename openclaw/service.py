#!/usr/bin/env python3
"""
OpenClaw Worker Service (local runner)

- Watches a local folder for file changes.
- Calls /api/openclaw/sync-folder when files change.
- Provides CLI to call /api/openclaw/ask.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path
from typing import Dict, Tuple
from urllib import error, request


DEFAULT_EXTENSIONS = (".pdf", ".txt", ".md")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local OpenClaw worker service.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Clinical Navigator base URL.")
    parser.add_argument("--secret", required=True, help="OPENCLAW_SHARED_SECRET value.")
    parser.add_argument("--watch-dir", required=True, help="Directory to monitor for policy updates.")
    parser.add_argument("--poll-seconds", type=float, default=5.0, help="Polling interval in seconds.")
    parser.add_argument("--extensions", default=",".join(DEFAULT_EXTENSIONS), help="Comma list of extensions.")
    parser.add_argument("--user-role", default="admin", help="x-user-role header for RBAC-enabled servers.")

    subparsers = parser.add_subparsers(dest="cmd")
    subparsers.add_parser("watch", help="Run folder watcher and sync loop")
    ask = subparsers.add_parser("ask", help="Send one question via /api/openclaw/ask")
    ask.add_argument("--question", required=True)
    ask.add_argument("--mode", default="long_context", choices=["rag", "long_context"])
    ask.add_argument("--top-k", type=int, default=8)
    ask.add_argument("--benchmark", action="store_true")
    return parser.parse_args()


def post_json(url: str, payload: dict, secret: str, role: str, timeout: float = 180.0) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-openclaw-secret": secret,
            "x-user-role": role,
        },
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw)


def get_json(url: str, secret: str, role: str, timeout: float = 30.0) -> dict:
    req = request.Request(
        url,
        headers={
            "x-openclaw-secret": secret,
            "x-user-role": role,
        },
        method="GET",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw)


class Worker:
    def __init__(self, base_url: str, secret: str, watch_dir: Path, extensions: Tuple[str, ...], role: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self.watch_dir = watch_dir
        self.extensions = tuple(x.lower() for x in extensions)
        self.role = role
        self.running = True
        self.snapshot: Dict[str, Tuple[float, int]] = {}

    def stop(self, *_: object) -> None:
        self.running = False

    def run(self, poll_seconds: float) -> int:
        if not self.watch_dir.exists() or not self.watch_dir.is_dir():
            print("Watch dir not found: %s" % self.watch_dir, file=sys.stderr)
            return 2

        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        try:
            hello = get_json(
                "%s/api/openclaw/handshake" % self.base_url,
                secret=self.secret,
                role=self.role,
            )
            print("Handshake OK:", hello)
        except Exception as exc:
            print("Handshake failed: %s" % exc, file=sys.stderr)
            return 1

        self.snapshot = self._scan()
        self._sync(reason="startup")
        print("Watching %s every %.1fs ..." % (self.watch_dir, poll_seconds))

        while self.running:
            current = self._scan()
            if current != self.snapshot:
                self.snapshot = current
                self._sync(reason="filesystem_change")
            time.sleep(poll_seconds)

        print("Worker stopped.")
        return 0

    def ask(self, question: str, mode: str, top_k: int, benchmark: bool) -> int:
        payload = {
            "question": question,
            "mode": mode,
            "top_k": top_k,
            "benchmark": benchmark,
        }
        try:
            result = post_json(
                "%s/api/openclaw/ask" % self.base_url,
                payload=payload,
                secret=self.secret,
                role=self.role,
                timeout=240.0,
            )
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            print("HTTP %s: %s" % (exc.code, body), file=sys.stderr)
            return 1
        except Exception as exc:
            print("Ask failed: %s" % exc, file=sys.stderr)
            return 1

    def _scan(self) -> Dict[str, Tuple[float, int]]:
        state: Dict[str, Tuple[float, int]] = {}
        for p in sorted(self.watch_dir.iterdir()):
            if not p.is_file():
                continue
            if p.suffix.lower() not in self.extensions:
                continue
            stat = p.stat()
            state[p.name] = (stat.st_mtime, stat.st_size)
        return state

    def _sync(self, reason: str) -> None:
        payload = {
            "folder_path": str(self.watch_dir),
            "extensions": list(self.extensions),
        }
        try:
            result = post_json(
                "%s/api/openclaw/sync-folder" % self.base_url,
                payload=payload,
                secret=self.secret,
                role=self.role,
                timeout=240.0,
            )
            print("Sync (%s): ingested=%s" % (reason, result.get("ingested_count")))
        except Exception as exc:
            print("Sync error (%s): %s" % (reason, exc), file=sys.stderr)


def main() -> int:
    args = parse_args()
    watch_dir = Path(args.watch_dir).expanduser().resolve()
    extensions = tuple(x.strip().lower() for x in args.extensions.split(",") if x.strip())
    worker = Worker(
        base_url=args.base_url,
        secret=args.secret,
        watch_dir=watch_dir,
        extensions=extensions,
        role=args.user_role,
    )

    if args.cmd == "ask":
        return worker.ask(
            question=args.question,
            mode=args.mode,
            top_k=args.top_k,
            benchmark=args.benchmark,
        )

    # Default behavior and explicit "watch" subcommand both run the watcher loop.
    return worker.run(poll_seconds=args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
