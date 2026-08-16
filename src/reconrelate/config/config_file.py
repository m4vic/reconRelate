"""Persistent CLI config — the Claude-Code-style ``config set/show`` layer.

Design: the config file is just **persisted environment variables** under
``~/.reconrelate/config.json`` (same names ``Settings.from_env`` and the provider
registry already read). At startup ``apply_config_to_env`` loads them into ``os.environ``
*without* overriding real env vars, so precedence is simply **env var > config file >
built-in default** and nothing downstream has to change.

Users don't type raw env names, though — ``config set`` accepts friendly keys that map
onto those env vars:

    reconrelate config set model qwen2.5:7b-instruct        # -> LLM_MODEL
    reconrelate config set allow_cloud false        # -> RECONRELATE_LLM_ALLOW_CLOUD
    reconrelate config set key.WHOXY_API_KEY sk-...  # -> WHOXY_API_KEY  (secret; stored 0600)
    reconrelate config set source.reverse_whois whoxy  # -> RECONRELATE_SOURCE_REVERSE_WHOIS

Defaults out of the box = free + local (no keys, local Ollama), exactly as intended.
Secrets are stored in a 0600 file and masked on ``config show``; they are never logged.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

_PATH_ENV = "RECONRELATE_CONFIG_PATH"  # override the config location (used by tests)
_DEFAULT_PATH = Path.home() / ".reconrelate" / "config.json"
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Friendly key -> underlying env var name. This is the entire user-facing surface for
# scalar settings; API keys go through ``key.*`` and source pins through ``source.*``.
_ALIASES = {
    "model": "LLM_MODEL",
    "fast_model": "FAST_LLM_MODEL",
    "allow_cloud": "RECONRELATE_LLM_ALLOW_CLOUD",
    "api_base": "OLLAMA_API_BASE",
    "run_mode": "RECONRELATE_RUN_MODE",
    "escalate_only": "RECONRELATE_LLM_ESCALATE_ONLY",
    "expand_acquisitions": "RECONRELATE_EXPAND_ACQUISITIONS",
    "historical_web": "RECONRELATE_HISTORICAL_WEB",
    "cache_ttl_hours": "RECONRELATE_CACHE_TTL_HOURS",
    "budget": "RECONRELATE_BUDGET",
    "profile": "RECONRELATE_PROVIDER_TIER",
    "max_provider_calls": "RECONRELATE_MAX_PROVIDER_CALLS",
    "max_billable_units": "RECONRELATE_MAX_BILLABLE_UNITS",
    "max_model_calls": "RECONRELATE_MAX_MODEL_CALLS",
    "max_model_input_tokens": "RECONRELATE_MAX_MODEL_INPUT_TOKENS",
    "max_model_output_tokens": "RECONRELATE_MAX_MODEL_OUTPUT_TOKENS",
    "max_cloud_tokens": "RECONRELATE_MAX_CLOUD_TOKENS",
    "max_cloud_cost_usd": "RECONRELATE_MAX_CLOUD_COST_USD",
    "db_path": "RECONRELATE_DB_PATH",
}

# Well-known API-key env names surfaced in `config show` (others still work via key.*).
_KNOWN_KEYS = ("WHOXY_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY")
_SECRET_SUFFIXES = ("_API_KEY", "_KEY", "_TOKEN", "_SECRET", "_PASSWORD")


def config_path() -> Path:
    override = os.getenv(_PATH_ENV)
    return Path(override) if override else _DEFAULT_PATH


def load_config() -> dict[str, str]:
    """Read the config file as ``{ENV_NAME: value}`` (empty dict if missing/corrupt)."""
    path = config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def save_config(cfg: dict[str, str]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n", "utf-8")
    os.replace(tmp, path)
    _restrict_perms(path)


def _restrict_perms(path: Path) -> None:
    """Best-effort 0600 file / 0700 dir — the file can hold API keys. No-op on Windows."""
    if os.name == "nt":
        return
    for target, mode in ((path.parent, 0o700), (path, 0o600)):
        try:
            os.chmod(target, mode)
        except OSError:
            pass


def apply_config_to_env(cfg: dict[str, str] | None = None, env: dict[str, str] | None = None) -> None:
    """Load persisted config into the environment. Real env vars always win (not overwritten)."""
    target = os.environ if env is None else env
    source = load_config() if cfg is None else cfg
    for name, value in source.items():
        if name not in target:
            target[name] = value


def resolve_key(user_key: str) -> str:
    """Map a user-facing config key to its underlying env-var name. Raises ValueError if unknown."""
    raw = user_key.strip()
    low = raw.lower()
    if low in _ALIASES:
        return _ALIASES[low]
    if low.startswith("key."):
        name = raw[4:].strip().upper()
        if not _ENV_NAME_RE.match(name):
            raise ValueError(f"invalid key name {name!r} (expected e.g. key.WHOXY_API_KEY)")
        return name
    if low.startswith("source."):
        cap = raw[7:].strip().lower()
        if not cap:
            raise ValueError("source.<capability> requires a capability (e.g. source.reverse_whois)")
        return f"RECONRELATE_SOURCE_{cap.upper()}"
    raise ValueError(
        f"unknown config key {user_key!r}. Valid keys: "
        + ", ".join(sorted(_ALIASES)) + ", key.<ENV_NAME>, source.<capability>"
    )


def set_value(user_key: str, value: str) -> str:
    env_name = resolve_key(user_key)
    cfg = load_config()
    cfg[env_name] = value
    save_config(cfg)
    return env_name


def unset_value(user_key: str) -> str:
    env_name = resolve_key(user_key)
    cfg = load_config()
    cfg.pop(env_name, None)
    save_config(cfg)
    return env_name


def is_secret(env_name: str) -> bool:
    return env_name.endswith(_SECRET_SUFFIXES)


def mask(value: str) -> str:
    # ASCII only — non-ASCII (…, —) turns into mojibake on the Windows cp1252 console.
    if not value:
        return ""
    return "****" if len(value) <= 4 else "..." + value[-4:]


def render_show(settings) -> str:
    """Human-readable effective config: scalar settings + API keys + source pins."""
    cfg = load_config()
    lines: list[str] = [f"Config file: {config_path()}", ""]

    lines.append("Effective settings (env var > config file > default):")
    fields = [
        ("model", settings.llm_model or "qwen2.5:7b-instruct (default)"),
        ("fast_model", settings.fast_model or "(none)"),
        ("api_base", settings.ollama_api_base),
        ("allow_cloud", str(settings.llm_allow_cloud)),
        ("run_mode", settings.run_mode),
        ("escalate_only", str(settings.llm_escalate_only)),
        ("expand_acquisitions", str(settings.expand_acquisitions)),
        ("historical_web", str(settings.historical_web)),
        ("profile", settings.provider_tier),
        ("max_provider_calls", str(settings.max_provider_calls)),
        ("max_billable_units", str(settings.max_billable_units)),
        ("max_model_calls", str(settings.max_model_calls)),
        ("max_model_input_tokens", str(settings.max_model_input_tokens)),
        ("max_model_output_tokens", str(settings.max_model_output_tokens)),
        ("max_cloud_tokens", str(settings.max_cloud_tokens)),
        ("max_cloud_cost_usd", str(settings.max_cloud_cost_usd)),
        ("cache_ttl_hours", str(settings.cache_ttl_hours)),
    ]
    for label, value in fields:
        lines.append(f"  {label:<20} {value}")

    lines.append("")
    lines.append("API keys:")
    key_names = list(_KNOWN_KEYS) + [k for k in cfg if is_secret(k) and k not in _KNOWN_KEYS]
    shown_any = False
    for name in key_names:
        value = os.getenv(name) or cfg.get(name, "")
        if value:
            lines.append(f"  {name:<24} {mask(value)} (set)")
            shown_any = True
    if not shown_any:
        lines.append("  (none set - running free/local)")

    lines.append("")
    lines.append("Source pins:")
    pins = {
        n[len("RECONRELATE_SOURCE_"):].lower(): (os.getenv(n) or cfg.get(n, ""))
        for n in set(cfg) | set(os.environ)
        if n.startswith("RECONRELATE_SOURCE_")
    }
    pins = {k: v for k, v in pins.items() if v}
    if pins:
        for cap, src in sorted(pins.items()):
            lines.append(f"  {cap:<20} {src}")
    else:
        lines.append("  (none - all sources auto)")

    lines.append("")
    lines.append("Note: real environment variables override the config file.")
    lines.append("Run `reconrelate providers` to see which sources are active.")
    return "\n".join(lines)
