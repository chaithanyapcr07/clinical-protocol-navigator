from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3-flash-preview"
    gemini_retry_max_attempts: int = 3
    gemini_retry_initial_delay_seconds: float = 20.0
    gemini_retry_backoff_multiplier: float = 2.0
    gemini_retry_max_delay_seconds: float = 75.0
    max_context_chars: int = 500000
    max_context_tokens: int = 120000
    context_profile: str = "balanced"
    max_context_chars_stress: int = 8000000
    max_context_tokens_stress: int = 1800000
    rag_top_k: int = 8
    enable_context_cache: bool = True
    context_cache_ttl_minutes: int = 1440
    context_cache_min_chars: int = 120000
    context_cache_index_path: str = "data/context_cache_index.json"
    enable_pii_redaction: bool = True
    rbac_enabled: bool = False
    rbac_header_name: str = "x-user-role"
    rbac_default_role: str = "viewer"
    audit_log_path: str = "data/audit/audit_log.jsonl"
    audit_hash_seed: str = "clinical-protocol-navigator"
    openclaw_shared_secret: str = ""
    openclaw_monitored_dir: str = ""
    openclaw_enable_folder_sync: bool = True
    openclaw_allowed_extensions: str = ".pdf,.txt,.md"
    benchmark_inter_mode_delay_seconds: float = 0.0

    def allowed_extensions(self) -> List[str]:
        values = [x.strip().lower() for x in self.openclaw_allowed_extensions.split(",")]
        return [x for x in values if x.startswith(".")]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
