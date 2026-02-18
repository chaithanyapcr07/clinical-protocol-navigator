from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from app.config import Settings

try:
    from google import genai
    from google.genai import types

    HAS_GENAI = True
except Exception:
    genai = None
    types = None
    HAS_GENAI = False


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = None
        self._cache_index_path = Path(self._settings.context_cache_index_path)
        self._cache_index: Dict[str, Dict[str, Any]] = {}

        if HAS_GENAI and self._settings.gemini_api_key:
            try:
                self._client = genai.Client(api_key=self._settings.gemini_api_key)
            except Exception:
                self._client = None

        self._load_cache_index()

    def estimate_tokens(self, text: str, fast: bool = True) -> int:
        estimate = max(1, len(text) // 4)
        if fast or not self._client:
            return estimate

        try:
            response = self._client.models.count_tokens(
                model=self._settings.gemini_model,
                contents=text,
            )
            total = getattr(response, "total_tokens", None)
            if total is None and isinstance(response, dict):
                total = response.get("total_tokens")
            if isinstance(total, int) and total > 0:
                return total
        except Exception:
            return estimate

        return estimate

    def answer(self, mode_name: str, question: str, context: str, use_cache: bool = True) -> str:
        if not self._settings.gemini_api_key:
            return (
                "LLM is not configured. Set GEMINI_API_KEY to enable generated answers. "
                "This response is a deterministic fallback from retrieved context."
            )

        if not HAS_GENAI or not self._client:
            return (
                "Gemini SDK is not available at runtime. Install dependencies and retry. "
                "Fallback mode is active."
            )

        system_instruction = (
            "You are a Clinical Policy Verification Engine. "
            "Identify conflicts, gaps, and compliance risks using only provided context. "
            "If evidence is insufficient, state that explicitly. "
            "Cite every key claim in [doc_name|page|paragraph] format."
        )

        cached_content_name = None
        if use_cache and self._should_use_cache(mode_name, context):
            cached_content_name = self._ensure_cached_content(context, mode_name)

        if cached_content_name:
            prompt_text = (
                f"QUESTION:\n{question}\n\n"
                "Return: (1) finding summary, (2) conflict risk, (3) remediation pointers."
            )
        else:
            prompt_text = (
                f"MODE: {mode_name}\n"
                f"QUESTION:\n{question}\n\n"
                "DOCUMENT CONTEXT:\n"
                f"{context}\n\n"
                "Return: (1) finding summary, (2) conflict risk, (3) remediation pointers."
            )

        try:
            response = self._generate_content(
                prompt_text=prompt_text,
                system_instruction=system_instruction,
                cached_content_name=cached_content_name,
            )
            text = (getattr(response, "text", None) or "").strip()
            if text:
                return text
            return "Model returned an empty response."
        except Exception as exc:
            message = str(exc).replace("\n", " ").strip()
            if len(message) > 240:
                message = message[:240] + "..."
            return "Gemini request failed (%s): %s. Fallback mode is active." % (type(exc).__name__, message)

    def _should_use_cache(self, mode_name: str, context: str) -> bool:
        if not self._settings.enable_context_cache:
            return False
        if not mode_name.lower().startswith("long"):
            return False
        return len(context) >= self._settings.context_cache_min_chars

    def _generate_content(
        self,
        prompt_text: str,
        system_instruction: str,
        cached_content_name: Optional[str],
    ) -> Any:
        model = self._settings.gemini_model

        if not cached_content_name:
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.0,
                max_output_tokens=1200,
            )
            return self._call_with_backoff(
                lambda: self._client.models.generate_content(
                    model=model,
                    contents=prompt_text,
                    config=config,
                )
            )

        # Try cache binding styles for SDK/API compatibility.
        attempts = [
            lambda: self._client.models.generate_content(
                model=model,
                contents=prompt_text,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.0,
                    max_output_tokens=1200,
                    cached_content=cached_content_name,
                ),
            ),
            lambda: self._client.models.generate_content(
                model=model,
                contents=prompt_text,
                cached_content=cached_content_name,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.0,
                    max_output_tokens=1200,
                ),
            ),
            lambda: self._client.models.generate_content(
                model=model,
                contents=prompt_text,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.0,
                    max_output_tokens=1200,
                ),
            ),
        ]

        last_error = None
        for attempt in attempts:
            try:
                return self._call_with_backoff(attempt)
            except Exception as exc:
                last_error = exc
        raise last_error

    def _ensure_cached_content(self, context: str, mode_name: str) -> Optional[str]:
        context_hash = self._context_hash(context)
        cached = self._cache_index.get(context_hash)

        if cached and self._cache_entry_valid(cached):
            return cached.get("name")

        created_name = self._create_cached_content(context, mode_name, context_hash)
        if not created_name:
            return None

        expires_at = int(time.time()) + (self._settings.context_cache_ttl_minutes * 60)
        self._cache_index[context_hash] = {
            "name": created_name,
            "expires_at": expires_at,
            "mode": mode_name,
            "model": self._settings.gemini_model,
        }
        self._save_cache_index()
        return created_name

    def _create_cached_content(self, context: str, mode_name: str, context_hash: str) -> Optional[str]:
        if not self._client or not hasattr(self._client, "caches"):
            return None

        ttl_value = "%ss" % (self._settings.context_cache_ttl_minutes * 60)
        display_name = "clinical-%s-%s" % (mode_name.lower(), context_hash[:10])

        attempts = []

        if hasattr(types, "CreateCachedContentConfig"):
            attempts.append(
                lambda: self._client.caches.create(
                    model=self._settings.gemini_model,
                    config=types.CreateCachedContentConfig(
                        display_name=display_name,
                        contents=[context],
                        ttl=ttl_value,
                    ),
                )
            )
            attempts.append(
                lambda: self._client.caches.create(
                    model=self._settings.gemini_model,
                    config=types.CreateCachedContentConfig(
                        display_name=display_name,
                        contents=[types.Part.from_text(text=context)],
                        ttl=ttl_value,
                    ),
                )
            )

        attempts.append(
            lambda: self._client.caches.create(
                model=self._settings.gemini_model,
                display_name=display_name,
                contents=[context],
                ttl=ttl_value,
            )
        )

        for attempt in attempts:
            try:
                created = self._call_with_backoff(attempt)
                name = self._extract_cache_name(created)
                if name:
                    return name
            except Exception:
                continue

        return None

    def _call_with_backoff(self, fn: Callable[[], Any]) -> Any:
        max_attempts = max(1, self._settings.gemini_retry_max_attempts)
        delay = max(0.0, self._settings.gemini_retry_initial_delay_seconds)
        multiplier = max(1.0, self._settings.gemini_retry_backoff_multiplier)
        max_delay = max(delay, self._settings.gemini_retry_max_delay_seconds)

        for attempt in range(1, max_attempts + 1):
            try:
                return fn()
            except Exception as exc:
                if attempt >= max_attempts or not self._is_retryable(exc):
                    raise
                if delay > 0:
                    time.sleep(delay)
                if delay == 0:
                    delay = 1.0
                else:
                    delay = min(max_delay, delay * multiplier)

        # Unreachable path, loop either returns or raises.
        raise RuntimeError("Retry loop terminated unexpectedly.")

    def _is_retryable(self, exc: Exception) -> bool:
        text = str(exc).lower()
        signals = (
            "429",
            "resource_exhausted",
            "rate limit",
            "rate-limit",
            "quota",
            "too many requests",
            "temporarily unavailable",
            "deadline exceeded",
        )
        return any(token in text for token in signals)

    def _extract_cache_name(self, created: Any) -> Optional[str]:
        name = getattr(created, "name", None)
        if isinstance(name, str) and name:
            return name
        if isinstance(created, dict):
            value = created.get("name")
            if isinstance(value, str) and value:
                return value
        return None

    def _context_hash(self, context: str) -> str:
        digest = hashlib.sha256()
        digest.update(self._settings.gemini_model.encode("utf-8"))
        digest.update(b"\n")
        digest.update(context.encode("utf-8", errors="ignore"))
        return digest.hexdigest()

    def _cache_entry_valid(self, entry: Dict[str, Any]) -> bool:
        if entry.get("model") != self._settings.gemini_model:
            return False
        expires_at = entry.get("expires_at")
        if not isinstance(expires_at, int):
            return False
        return expires_at > int(time.time())

    def _load_cache_index(self) -> None:
        if not self._cache_index_path.exists():
            return

        try:
            data = json.loads(self._cache_index_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._cache_index = data
        except Exception:
            self._cache_index = {}

    def _save_cache_index(self) -> None:
        try:
            self._cache_index_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_index_path.write_text(
                json.dumps(self._cache_index, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception:
            # Cache persistence failures should never break answering.
            pass
