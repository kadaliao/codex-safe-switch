"""TOML provider-section helper for codex-switch.

Defines which keys belong to a "provider" (and thus get swapped between profiles)
vs which are local state (preserved across switches).
"""

import sys
from pathlib import Path

import tomlkit

# Top-level scalar keys that belong to a provider profile.
PROVIDER_TOP_KEYS = {
    "model",
    "model_provider",
    "model_reasoning_effort",
    "model_reasoning_summary",
    "model_verbosity",
    "wire_api",
    "disable_response_storage",
    "preferred_auth_method",
}

# Top-level tables that belong to a provider profile.
PROVIDER_TABLES = {"model_providers"}


def load(path: str):
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return tomlkit.document()
    return tomlkit.parse(p.read_text())


def extract(src: str, dst: str) -> None:
    """Write the provider-related slice of src into dst as a standalone toml."""
    doc = load(src)
    out = tomlkit.document()
    for k in PROVIDER_TOP_KEYS:
        if k in doc:
            out[k] = doc[k]
    for t in PROVIDER_TABLES:
        if t in doc:
            out[t] = doc[t]
    Path(dst).write_text(tomlkit.dumps(out))


def merge(current: str, profile_provider: str, out_path: str) -> None:
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

    Path(out_path).write_text(tomlkit.dumps(current_doc))


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: _swap.py <extract|merge> ...", file=sys.stderr)
        return 2
    cmd = sys.argv[1]
    if cmd == "extract" and len(sys.argv) == 4:
        extract(sys.argv[2], sys.argv[3])
    elif cmd == "merge" and len(sys.argv) == 5:
        merge(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        print(f"bad invocation: {sys.argv}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
