# codex-profile-switcher

One-key switch between [OpenAI Codex CLI](https://github.com/openai/codex) configurations — official ChatGPT login, third-party relays, multiple API keys, whatever. CLI + optional Alfred workflow.

Each profile owns the *provider* slice of `~/.codex/config.toml` (model, `[model_providers.*]`, auth method) plus its own `auth.json`. Your local state (trusted projects, plugins, marketplaces, MCP servers, TUI prefs) is left untouched on every switch.

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
codex-switch official     # switch back to official OpenAI ChatGPT login
codex-switch openai       # alias of `official`
codex-switch use [name]   # load <name>; omit for the picker
codex-switch save <name>  # snapshot the current ~/.codex state as <name>
codex-switch show <name>  # print <name>'s provider.toml + auth.json key names
codex-switch state <name> # show/set the session-state scope for a profile
codex-switch rm <name>    # delete profile (the active one is protected)
codex-switch alfred-list  # JSON for Alfred Script Filter
```

The picker auto-falls back to a numeric menu when stdin/stdout aren't TTYs (pipes, scripts).

## Official OpenAI shortcut

`codex-switch official` is the one-step way back to the official OpenAI ChatGPT login.

- The tool keeps a hidden `~/.codex/profiles/.official/` snapshot for the official config/auth.
- The first time you switch away from an official OpenAI session, that snapshot is refreshed automatically.
- `codex-switch openai` is an alias if you prefer typing the provider name directly.

## Shared history by default

After every `use` / `official` switch, `codex-switch` automatically aligns local Codex history metadata to the active provider and model identity.

- You no longer need to remember `merge-history` during normal profile switching.
- This keeps session history visible when moving between relay profiles and the official OpenAI login, including surfaces that filter by model id.
- `merge-history --keep-models` still exists if you want a provider-only repair and need to preserve historical per-thread model ids.

## Profile format

```text
~/.codex/profiles/
├── .active                       # plaintext: name of the active profile
├── chatgpt-official/
│   ├── auth.json                 # full file copied into ~/.codex/auth.json
│   └── provider.toml             # empty = use ChatGPT login
└── myrelay/
    ├── auth.json                 # {"OPENAI_API_KEY": "sk-..."}
    └── provider.toml             # only provider-related keys (see examples/)
```

The following top-level keys + tables are owned by a profile (swapped on `use`); everything else in `~/.codex/config.toml` is preserved:

- `model`, `model_provider`, `model_reasoning_effort`, `model_reasoning_summary`, `model_verbosity`
- `wire_api`, `disable_response_storage`, `preferred_auth_method`
- `[model_providers.*]`

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
