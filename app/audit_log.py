from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


class AuditLogger:
    def __init__(self, log_path: str, hash_seed: str) -> None:
        self.log_path = Path(log_path)
        self.hash_seed = hash_seed
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._last_hash = self._load_last_hash()

    def append(self, event_type: str, payload: Dict[str, Any]) -> str:
        timestamp = datetime.now(timezone.utc).isoformat()
        serialized_payload = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        base = "%s|%s|%s|%s|%s" % (
            self.hash_seed,
            self._last_hash,
            timestamp,
            event_type,
            serialized_payload,
        )
        entry_hash = hashlib.sha256(base.encode("utf-8")).hexdigest()
        record = {
            "ts_utc": timestamp,
            "event_type": event_type,
            "payload": payload,
            "prev_hash": self._last_hash,
            "entry_hash": entry_hash,
        }

        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        self._last_hash = entry_hash
        return entry_hash

    def verify(self) -> Dict[str, Any]:
        if not self.log_path.exists():
            return {"ok": True, "entries": 0, "last_hash": ""}

        expected_prev = ""
        entries = 0
        with self.log_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entries += 1
                record = json.loads(line)
                if record.get("prev_hash", "") != expected_prev:
                    return {
                        "ok": False,
                        "entries": entries,
                        "last_hash": expected_prev,
                        "error": "Broken hash chain at line %s" % entries,
                    }

                recomputed = self._recompute_hash(record)
                if recomputed != record.get("entry_hash", ""):
                    return {
                        "ok": False,
                        "entries": entries,
                        "last_hash": expected_prev,
                        "error": "Hash mismatch at line %s" % entries,
                    }
                expected_prev = record.get("entry_hash", "")

        return {"ok": True, "entries": entries, "last_hash": expected_prev}

    def _recompute_hash(self, record: Dict[str, Any]) -> str:
        serialized_payload = json.dumps(record.get("payload", {}), sort_keys=True, ensure_ascii=False)
        base = "%s|%s|%s|%s|%s" % (
            self.hash_seed,
            record.get("prev_hash", ""),
            record.get("ts_utc", ""),
            record.get("event_type", ""),
            serialized_payload,
        )
        return hashlib.sha256(base.encode("utf-8")).hexdigest()

    def _load_last_hash(self) -> str:
        if not self.log_path.exists():
            return ""

        last_hash = ""
        with self.log_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    value = obj.get("entry_hash", "")
                    if isinstance(value, str):
                        last_hash = value
                except Exception:
                    continue
        return last_hash
