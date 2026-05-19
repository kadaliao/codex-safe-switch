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
codex-switch ls            # list profiles, ★ marks the active one
codex-switch current       # print the active profile
codex-switch use <name>    # load <name> into ~/.codex/{config.toml,auth.json}
codex-switch save <name>   # snapshot the current ~/.codex state as <name>
codex-switch show <name>   # print <name>'s provider.toml + auth.json key names
codex-switch rm <name>     # delete profile (the active one is protected)
codex-switch alfred-list   # JSON for Alfred Script Filter
```

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
