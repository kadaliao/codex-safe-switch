"""TOML provider-section helper for codex-safe-switch.

Defines which keys belong to a "provider" (and thus get swapped between profiles)
vs which are local state (preserved across switches).
"""

from __future__ import annotations

from pathlib import Path

import tomlkit

# Top-level scalar keys that belong to a provider profile.
PROVIDER_TOP_KEYS = frozenset({
    "model",
    "model_provider",
    "model_reasoning_effort",
    "model_reasoning_summary",
    "model_verbosity",
    "wire_api",
    "disable_response_storage",
    "preferred_auth_method",
})

# Top-level tables that belong to a provider profile.
PROVIDER_TABLES = frozenset({"model_providers"})


def load(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return tomlkit.document()
    return tomlkit.parse(path.read_text())


def extract(src: Path, dst: Path) -> None:
    """Write the provider-related slice of src into dst as a standalone toml."""
    doc = load(src)
    out = tomlkit.document()
    for k in PROVIDER_TOP_KEYS:
        if k in doc:
            out[k] = doc[k]
    for t in PROVIDER_TABLES:
        if t in doc:
            out[t] = doc[t]
    dst.write_text(tomlkit.dumps(out))


def merge(current: Path, profile_provider: Path, out_path: Path) -> None:
    """Strip provider section from current config, then append profile's provider.toml.

    Result keeps all local state (projects.*, tui.*, plugins.*, marketplaces.*, ...)
    and adopts the profile's provider settings.
    """
    current_doc = load(current)
    profile_doc = load(profile_provider)

    for k in PROVIDER_TOP_KEYS:
        if k in current_doc:
            del current_doc[k]
    for t in PROVIDER_TABLES:
        if t in current_doc:
            del current_doc[t]

    for k in profile_doc:
        # tomlkit Document doesn't allow re-parenting a value owned by another doc
        # cleanly, so round-trip through dumps/parse for safety.
        current_doc[k] = tomlkit.parse(tomlkit.dumps({k: profile_doc[k]}))[k]

    out_path.write_text(tomlkit.dumps(current_doc))
