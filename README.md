# codex-profile-switcher

One-key switch between [OpenAI Codex CLI](https://github.com/openai/codex) configurations — official ChatGPT login, third-party relays, multiple API keys, whatever. CLI + optional Alfred workflow.

Each profile owns the *provider* slice of `~/.codex/config.toml` (model, `[model_providers.*]`, auth method) plus its own `auth.json`. By default your local state (trusted projects, plugins, marketplaces, MCP servers, TUI prefs, history DB/index) is left untouched on every switch.

If you want history to follow a provider/model family instead of one global Codex state, profiles can also opt into a session-state scope. That makes `codex-switch` save/restore the visible history files (`state_5.sqlite`, `history.jsonl`, `session_index.jsonl`) when you switch.

## Install

Requires [`uv`](https://github.com/astral-sh/uv).

```bash
uv tool install git+https://github.com/kadaliao/codex-profile-switcher.git
```

This puts `codex-switch` on `$PATH` (default `~/.local/bin/`). Run `uv tool update-shell` once if your shell can't find it.

Upgrade later with `uv tool upgrade codex-profile-switcher`; uninstall with `uv tool uninstall codex-profile-switcher`.

### No-install (one-off)

```bash
uvx --from git+https://github.com/kadaliao/codex-profile-switcher.git codex-switch ls
```

`uvx` resolves and caches an ephemeral environment per invocation. Convenient for trying it out, slower for hot paths like Alfred — use `uv tool install` if you want the workflow to feel snappy.

### Alfred (optional)

After `uv tool install`, double-click `alfred/codex-profile-switcher.alfredworkflow`. Trigger with keyword `cx`.

The workflow calls `$HOME/.local/bin/codex-switch`; if `uv tool install` put the binary elsewhere (`uv tool dir --bin` to check), edit the two `script` blocks in the workflow's plist accordingly.

## CLI

```text
codex-switch              # interactive picker (↑/↓, enter to switch, q to cancel)
codex-switch ls           # list profiles, ★ marks the active one
codex-switch current      # print the active profile
codex-switch use [name]   # load <name>; omit for the picker
codex-switch save <name>  # snapshot the current ~/.codex state as <name>
codex-switch save <name> --scope relay
                         # snapshot + seed a session-state scope immediately
codex-switch show <name>  # print <name>'s provider.toml + auth.json key names
codex-switch state <name> # show or set <name>'s session-state scope
codex-switch merge-history
                         # rewrite local history to current provider/model identity
codex-switch rm <name>    # delete profile (the active one is protected)
codex-switch alfred-list  # JSON for Alfred Script Filter
```

The picker auto-falls back to a numeric menu when stdin/stdout aren't TTYs (pipes, scripts).

## Profile format

```text
~/.codex/profiles/
├── .active                       # plaintext: name of the active profile
├── chatgpt-official/
│   ├── auth.json                 # full file copied into ~/.codex/auth.json
│   ├── provider.toml             # empty = use ChatGPT login
│   └── session.toml              # optional; omitted = share one global history state
└── myrelay/
    ├── auth.json                 # {"OPENAI_API_KEY": "sk-..."}
    └── provider.toml             # only provider-related keys (see examples/)
```

The following top-level keys + tables are owned by a profile (swapped on `use`); everything else in `~/.codex/config.toml` is preserved:

- `model`, `model_provider`, `model_reasoning_effort`, `model_reasoning_summary`, `model_verbosity`
- `wire_api`, `disable_response_storage`, `preferred_auth_method`
- `[model_providers.*]`

## Session-state scopes

Codex's history visibility can split when `model_provider` or `model` changes. The clean fix is to treat "backend config" and "history state" as separate concerns:

- Keep one shared scope for profiles that should share visible history.
- Give incompatible profiles their own scope so switching back restores the matching history DB/index.

Configure it with:

```bash
codex-switch save douban --scope relay
codex-switch state aihezu --scope relay
codex-switch state chatgpt-official --scope openai
```

That creates `session.toml` like:

```toml
mode = "scoped"
scope = "relay"
```

When two profiles use the same scope, they share the same `history.jsonl`, `session_index.jsonl`, and `state_5.sqlite`. When a profile uses a different scope, `codex-switch use` saves the current scope's files under `~/.codex/profiles/.session-state/<scope>/` and restores the target scope's files.

Disable state swapping again with:

```bash
codex-switch state <name> --shared
```

Suggested pattern:

- Relay/API-key profiles that should feel like one Codex identity: put them on the same scope, for example `relay`.
- Official `openai` / ChatGPT-login profiles: usually give them a separate scope, for example `openai`.

This avoids brittle SQLite/JSONL rewrites and makes switching deterministic.

## Merge History

If you really want to collapse existing history across identities, use:

```bash
codex-switch merge-history
```

That rewrites local rollout JSONL metadata plus `state_5.sqlite` so they match the current `~/.codex/config.toml` identity. By default it normalizes both:

- `model_provider`
- `model`

If you only want to unify official `openai` and third-party `crs` under one provider identity but keep historical model values as-is, use:

```bash
codex-switch merge-history --provider crs --keep-models
```

Notes:

- A timestamped backup is created under `~/.codex/history-merge-backup-*`.
- `--keep-models` is weaker than a full merge. If your Codex build also partitions visible history by model, you may still need a second run without `--keep-models` for the model you want to browse now.
- This command is intentionally explicit and separate from normal `save/use/state` flows.

## Adding a relay profile

1. Configure the relay normally in `~/.codex/{config.toml,auth.json}` and verify `codex` works.
2. `codex-switch save <name>` — snapshots the provider slice + auth.json into a new profile.
3. `cx` in Alfred (or `codex-switch use <name>`) to switch anytime.

Or build the files by hand — see `examples/relay-profile/`.

## Env overrides

| Var                  | Default              | Purpose                           |
| -------------------- | -------------------- | --------------------------------- |
| `CODEX_PROFILE_ROOT` | `~/.codex/profiles`  | where profiles live               |
| `CODEX_HOME`         | `~/.codex`           | the codex config dir to write     |

## License

MIT
