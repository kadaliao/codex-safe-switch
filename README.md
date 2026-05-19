# codex-profile-switcher

One-key switch between [OpenAI Codex CLI](https://github.com/openai/codex) configurations — official ChatGPT login, third-party relays, multiple API keys, whatever. CLI + optional Alfred workflow.

Each profile owns the *provider* slice of `~/.codex/config.toml` (model, `[model_providers.*]`, auth method) plus its own `auth.json`. Your local state (trusted projects, plugins, marketplaces, MCP servers, TUI prefs) is left untouched on every switch.

## Install

```bash
git clone https://github.com/kadaliao/codex-profile-switcher.git ~/.codex/profiles-src
mkdir -p ~/.codex/profiles/bin
cp ~/.codex/profiles-src/bin/* ~/.codex/profiles/bin/
chmod +x ~/.codex/profiles/bin/codex-switch
# optional: put it on $PATH
ln -sf ~/.codex/profiles/bin/codex-switch /usr/local/bin/codex-switch
```

Requires [`uv`](https://github.com/astral-sh/uv) on `$PATH` — it loads `tomlkit` on demand for TOML round-tripping.

### Alfred (optional)

Double-click `alfred/codex-profile-switcher.alfredworkflow` to install. Trigger with keyword `cx`.

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
├── bin/{codex-switch,_swap.py}
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
| `UV_BIN`             | `which uv`           | path to the `uv` binary           |

## License

MIT
