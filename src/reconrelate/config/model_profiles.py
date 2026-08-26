"""Named model profiles and role-based routing on top of the existing env-driven config.

A profile is `{name -> provider, model id, api_base, key reference, optional price}`. Two
roles exist today, matching the two call sites `RelationshipEngine`/`LLMClient` already have
(`llm_orchestration/relationship_engine.py`, the "economical-first-v1" policy): `primary` and
`fast`. Assigning a profile to `fast` makes it the first, cheaper attempt; `primary` is used
standalone, or on escalation when the fast attempt is weak or abstains. This is "manage Ollama
plus API models based on the task" applied to the one task-split that exists in the code today
— future phases that add more model-backed steps (org resolution, graph adjudication) extend
the same role mechanism rather than inventing a parallel one.

Profiles are stored as a single JSON blob under one reserved config key
(``RECONRELATE_MODEL_PROFILES``) so the flat ``config.json`` / ``apply_config_to_env`` contract
in ``config_file.py`` is untouched — this module only ever calls its public ``load_config`` /
``save_config``. Activating a role expands into the same env vars ``Settings.from_env`` already
reads (``LLM_MODEL``, ``FAST_LLM_MODEL``, ``OLLAMA_API_BASE``), plus registers any user-supplied
cloud price envelope, so no other module needs to know profiles exist.

Assigning a cloud profile to a role never loosens the existing spend gates — a run still needs
``--approve-cloud``, ``allow_cloud``, and positive cloud-token/cost ceilings regardless of which
profile is active. Profiles pick *which* model; they do not grant permission to spend on it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from reconrelate.config import config_file as cf
from reconrelate.llm_orchestration import model_pricing
from reconrelate.llm_orchestration.relationship_engine import _litellm_model_id

PROFILES_ENV = "RECONRELATE_MODEL_PROFILES"
_SCHEMA_VERSION = 1
VALID_PROVIDERS = ("ollama", "openai", "anthropic", "gemini", "custom")
VALID_ROLES = ("primary", "fast")
_DEFAULT_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


class ModelProfileError(ValueError):
    """A profile add/use/remove request was invalid."""


def infer_provider(model_id: str) -> str:
    """Best-effort provider guess from a bare model id, for `model add` without --provider."""
    lowered = model_id.strip().lower()
    if lowered.startswith("gemini") or lowered.startswith("gemini/"):
        return "gemini"
    resolved = _litellm_model_id(model_id)
    if resolved.startswith("ollama/"):
        return "ollama"
    if resolved.startswith("anthropic/"):
        return "anthropic"
    prefix = resolved.split("/", 1)[0].lower() if "/" in resolved else ""
    if prefix in ("", "openai"):
        return "openai"  # bare gpt-*/o1/o3/chatgpt-* passthrough, or already openai/<model>
    return "custom"  # a recognized-but-uncommon cloud prefix (groq, mistral, ...) needs its own price


def default_key_env(provider: str) -> str:
    """The conventional API-key env var for a built-in provider, or "" if there isn't one.

    Never raises; an unknown provider (including "custom", which has no fixed key name) just
    gets "". Callers treat "" as "no default — ask the user or leave credential display blank",
    not as an error.
    """
    return _DEFAULT_KEY_ENV.get(provider, "")


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """One named `{provider, model, endpoint, price}` bundle a role can be assigned to.

    Immutable by design — `model add` always constructs a fresh instance (via `add_profile`,
    never this constructor directly outside tests) rather than mutating one in place, so a
    `ProfileStore` snapshot can be reasoned about without worrying a profile changed underfoot.

    Field semantics that aren't obvious from the names:
    - `key_env`: the env var this profile's credential is *expected* to live in. This module
      never reads or exports the secret itself — the actual value already flows through
      `config_file`'s existing `key.<NAME>` mechanism, since litellm reads provider keys
      (`OPENAI_API_KEY`, etc.) straight from the process environment. `key_env` is display/audit
      metadata only (see `credential_status`), not the credential.
    - `input_usd_per_million` / `output_usd_per_million`: `None` means "rely on the built-in
      price catalog in `model_pricing`", not "free" or "unpriced". A cloud profile with both
      `None` is only reachable if the model is already in that catalog — `add_profile` enforces
      this at creation time via `_require_price_envelope`.
    - `price_verified_on`: ISO date string, "" when the two price fields are `None`. Used by
      `model_pricing.register_price` to seed that entry's own staleness clock, independent of
      the built-in catalog's.
    """

    name: str
    provider: str
    model_id: str
    api_base: str = ""
    key_env: str = ""
    input_usd_per_million: float | None = None
    output_usd_per_million: float | None = None
    price_verified_on: str = ""

    def litellm_id(self) -> str:
        """The exact string passed to litellm and to price/budget lookups for this profile.

        Idempotent under _litellm_model_id — re-deriving from this result downstream (as
        LLMClient.call_unified does on every call) must reproduce the same string, or a custom
        profile could silently be re-classified as local Ollama the next time it's resolved.
        """
        if self.provider == "ollama":
            return f"ollama/{self.model_id.removeprefix('ollama/')}"
        if self.provider == "gemini":
            # _litellm_model_id has no rule for bare Gemini names ("gemini-3.6-flash" matches
            # neither the gpt-*/claude* prefixes nor a provider/ form), so without this it would
            # fall through to the ollama/ default and try to reach a local daemon.
            return self.model_id if "/" in self.model_id else f"gemini/{self.model_id}"
        if self.provider == "custom":
            # A bare custom model id has no provider prefix _litellm_model_id recognizes, so it
            # would otherwise fall through to "unrecognized string -> ollama/" and silently talk
            # to a local Ollama daemon instead. Route through litellm's generic openai/<model>
            # provider prefix + a custom api_base — the standard pattern for a self-hosted or
            # third-party OpenAI-compatible endpoint. A model id that already names a provider
            # (e.g. "together_ai/meta-llama/...") is left verbatim.
            return self.model_id if "/" in self.model_id else f"openai/{self.model_id}"
        return _litellm_model_id(self.model_id)

    def is_cloud(self) -> bool:
        """True if a run must clear the cloud spend gates (--approve-cloud, allow_cloud, positive
        token/cost ceilings) before this profile can be used. Not a statement about physical
        infrastructure — "custom" and "gemini" are always cloud in this sense even though a
        self-hosted "custom" endpoint might run on hardware the user owns.
        """
        return self.provider != "ollama"

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict form used for JSON persistence (`ProfileStore.to_json`) and `model show
        --json`. Field order and names are the dataclass's own — this is not a public wire
        schema with independent versioning."""
        return asdict(self)


@dataclass(slots=True)
class ProfileStore:
    """The full set of configured profiles plus the current role assignments.

    A plain in-memory value object: callers load one with `load_store()`, mutate it via
    `add_profile`/`remove_profile`/`use_profile` (which mutate the passed-in store rather than
    returning a new one), and persist changes explicitly with `save_store()`. Nothing in this
    module auto-saves — forgetting to call `save_store()` after a mutation is a caller bug, not
    something this module guards against.
    """

    profiles: dict[str, ModelProfile] = field(default_factory=dict)
    roles: dict[str, str] = field(default_factory=dict)  # role -> profile name

    def to_json(self) -> str:
        """Serialize to the schema `from_json` reads back. Sorted keys for stable diffs of the
        underlying config.json across saves."""
        import json
        return json.dumps({
            "schema_version": _SCHEMA_VERSION,
            "profiles": {name: p.to_dict() for name, p in self.profiles.items()},
            "roles": self.roles,
        }, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "ProfileStore":
        """Parse a persisted store. Deliberately tolerant except for one case:

        - "" or whitespace-only -> an empty store (the common case: no profiles configured yet).
        - malformed JSON -> raises ModelProfileError. This is the one case worth failing loud on,
          since it means the config file itself is corrupt.
        - a profile entry with an unrecognized shape (wrong types, extra/missing dataclass
          fields) is silently DROPPED rather than raising, so one bad entry can't make every
          profile inaccessible.
        - a role pointing at a profile name that doesn't exist (or was just dropped above) is
          silently removed from `roles`, never left dangling.
        """
        import json
        if not raw.strip():
            return cls()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ModelProfileError(f"corrupt model profile store: {exc}") from exc
        if not isinstance(data, dict):
            return cls()
        profiles: dict[str, ModelProfile] = {}
        for name, payload in (data.get("profiles") or {}).items():
            if not isinstance(payload, dict):
                continue
            try:
                profiles[name] = ModelProfile(**{
                    k: v for k, v in payload.items()
                    if k in ModelProfile.__dataclass_fields__
                })
            except TypeError:
                continue
        roles = {
            role: profile_name for role, profile_name in (data.get("roles") or {}).items()
            if profile_name in profiles
        }
        return cls(profiles=profiles, roles=roles)


def load_store() -> ProfileStore:
    """Read the persisted profile store from ~/.reconrelate/config.json (or
    RECONRELATE_CONFIG_PATH). Never raises for a missing file or missing key — returns an empty
    store, same as a fresh install. Can raise ModelProfileError if the stored JSON is corrupt
    (see ProfileStore.from_json)."""
    cfg = cf.load_config()
    return ProfileStore.from_json(cfg.get(PROFILES_ENV, ""))


def save_store(store: ProfileStore) -> None:
    """Persist `store`, replacing whatever was previously saved. Read-modify-write against the
    config file's *current* on-disk contents (not a cached copy), so a concurrent `config set`
    of an unrelated key is preserved rather than clobbered."""
    cfg = cf.load_config()
    cfg[PROFILES_ENV] = store.to_json()
    cf.save_config(cfg)


def _require_price_envelope(profile: ModelProfile) -> None:
    """Fail at `model add` time, not silently mid-run.

    Today an unpriced cloud call raises ModelPricingUnavailableError, which is a subclass of
    ModelBudgetExceededError — LLMClient._call_model catches that as status="budget_exceeded"
    and returns no pivots, visible only with --verbose. Refusing to save an unpriced cloud
    profile up front turns that into a loud, immediate error instead.
    """
    litellm_id = profile.litellm_id()
    has_price = model_pricing.has_price(litellm_id)
    has_custom_price = (
        profile.input_usd_per_million is not None and profile.output_usd_per_million is not None
    )
    if not (has_price or has_custom_price):
        raise ModelProfileError(
            f"model {profile.model_id!r} has no known price envelope; pass --input-price and "
            "--output-price (USD per million tokens) so cost ceilings can be enforced before "
            "any call is made"
        )


def add_profile(
    store: ProfileStore,
    *,
    name: str,
    provider: str,
    model_id: str,
    api_base: str = "",
    key_env: str = "",
    input_price: float | None = None,
    output_price: float | None = None,
) -> ModelProfile:
    """Build a ModelProfile, add it to `store` (in place, keyed by `name` — an existing profile
    of the same name is silently replaced, not merged), and return it. Does not call
    `save_store`; the caller persists.

    Raises ModelProfileError, without mutating `store`, if: `name` is blank after stripping;
    `provider` isn't one of VALID_PROVIDERS; `model_id` is blank; exactly one of
    `input_price`/`output_price` is given (both or neither is required); or the profile is a
    cloud provider with no resolvable price (see `_require_price_envelope`) — a run cannot
    reserve a budget for a model it has no price for, so this fails at registration time instead
    of silently mid-run.
    """
    name = name.strip()
    if not name:
        raise ModelProfileError("profile name is required")
    if provider not in VALID_PROVIDERS:
        raise ModelProfileError(f"--provider must be one of {', '.join(VALID_PROVIDERS)}")
    model_id = model_id.strip()
    if not model_id:
        raise ModelProfileError("model id is required")
    if (input_price is None) != (output_price is None):
        raise ModelProfileError("--input-price and --output-price must be given together")

    profile = ModelProfile(
        name=name,
        provider=provider,
        model_id=model_id,
        api_base=api_base.strip(),
        key_env=(key_env.strip().upper() or _DEFAULT_KEY_ENV.get(provider, "")),
        input_usd_per_million=input_price,
        output_usd_per_million=output_price,
        price_verified_on=date.today().isoformat() if input_price is not None else "",
    )
    if profile.is_cloud():
        _require_price_envelope(profile)

    store.profiles[name] = profile
    return profile


def remove_profile(store: ProfileStore, name: str) -> None:
    """Delete profile `name` from `store` in place, and clear it from any role it was assigned
    to — a role is never left pointing at a profile that no longer exists (mirrors the
    dangling-role cleanup ProfileStore.from_json does on load). Raises ModelProfileError, without
    mutating `store`, if `name` isn't present. Does not call `save_store`."""
    if name not in store.profiles:
        raise ModelProfileError(f"no such profile: {name!r}")
    del store.profiles[name]
    store.roles = {role: p for role, p in store.roles.items() if p != name}


def use_profile(store: ProfileStore, name: str, role: str = "primary") -> None:
    """Assign profile `name` to `role` in `store`, in place, replacing whatever was assigned to
    that role before. Raises ModelProfileError, without mutating `store`, if `role` isn't
    "primary"/"fast" or `name` isn't a profile already in `store` (add it first). Does not call
    `save_store`, and does not check credentials or spend gates — see `credential_status` and the
    cloud-approval flags in `core/factory.py` for those."""
    if role not in VALID_ROLES:
        raise ModelProfileError(f"--role must be one of {', '.join(VALID_ROLES)}")
    if name not in store.profiles:
        raise ModelProfileError(f"no such profile: {name!r}; run `reconrelate model add` first")
    store.roles[role] = name


def active_profile(store: ProfileStore, role: str) -> ModelProfile | None:
    """The profile currently assigned to `role`, or None if nothing is assigned (including an
    unrecognized `role` string) — never raises. `None` is the normal, expected state before the
    user has run `model use`."""
    return store.profiles.get(store.roles.get(role, ""))


def apply_profiles_to_env(store: ProfileStore | None = None, env: dict[str, str] | None = None) -> None:
    """Expand active role assignments into the env vars Settings.from_env() already reads.

    Called once at CLI startup, right after apply_config_to_env() and before Settings.from_env().
    Never overrides a real env var already set by the caller's shell — same precedence rule as
    apply_config_to_env. Does not touch API keys: those already flow through the existing
    `config set key.<NAME>` mechanism, since litellm reads OPENAI_API_KEY / ANTHROPIC_API_KEY
    from the process environment directly.
    """
    import os
    target = env if env is not None else os.environ
    store = store or load_store()

    def _apply_role(role: str, model_var: str) -> None:
        profile = active_profile(store, role)
        if profile is None:
            return
        # Always export the fully-resolved id, never the raw model_id: it is what makes a
        # "custom" profile idempotent under the re-derivation call_unified performs on every
        # LLM call (see ModelProfile.litellm_id). Harmless no-op for the other three providers.
        target.setdefault(model_var, profile.litellm_id())
        if profile.provider == "ollama" and profile.api_base:
            target.setdefault("OLLAMA_API_BASE", profile.api_base)
        if profile.provider == "custom" and profile.api_base:
            target.setdefault("RECONRELATE_LLM_CUSTOM_API_BASE", profile.api_base)
        if profile.input_usd_per_million is not None and profile.output_usd_per_million is not None:
            verified = date.fromisoformat(profile.price_verified_on) if profile.price_verified_on else None
            model_pricing.register_price(
                profile.litellm_id(), profile.input_usd_per_million, profile.output_usd_per_million,
                verified_on=verified,
            )

    _apply_role("primary", "LLM_MODEL")
    _apply_role("fast", "FAST_LLM_MODEL")


def credential_status(profile: ModelProfile) -> tuple[str, bool]:
    """(expected env var name, whether it is currently set) for display, not enforcement.

    `models doctor` remains the authority on whether a run can actually proceed.
    """
    import os
    if not profile.is_cloud():
        return "", True
    name = profile.key_env or _DEFAULT_KEY_ENV.get(profile.provider, "")
    if not name:
        return "", True  # e.g. a custom endpoint with no declared auth
    return name, bool(os.getenv(name) or cf.load_config().get(name))


def render_list(store: ProfileStore) -> str:
    """Human-readable table for `model list` / `config show` — never JSON, never raises, pure
    (reads `store` and, via `credential_status`, the environment and config file for
    presence-only key checks; makes no changes to either)."""
    if not store.profiles:
        return "No model profiles configured. Add one with `reconrelate model add`."
    lines = ["Model profiles:"]
    role_by_profile: dict[str, list[str]] = {}
    for role, name in store.roles.items():
        role_by_profile.setdefault(name, []).append(role)
    for name, profile in sorted(store.profiles.items()):
        roles = ",".join(sorted(role_by_profile.get(name, []))) or "-"
        key_name, key_ok = credential_status(profile)
        key_note = "" if not key_name else f", key {key_name} {'(set)' if key_ok else '(missing)'}"
        price_note = ""
        if profile.is_cloud():
            price_note = (
                f", ${profile.input_usd_per_million:g}/${profile.output_usd_per_million:g} per M"
                if profile.input_usd_per_million is not None else ", price: built-in catalog"
            )
        lines.append(
            f"  {name:<16} role={roles:<12} {profile.provider}/{profile.model_id}{key_note}{price_note}"
        )
    return "\n".join(lines)
