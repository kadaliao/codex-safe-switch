# codex-safe-switch

[![PyPI](https://img.shields.io/pypi/v/codex-safe-switch.svg)](https://pypi.org/project/codex-safe-switch/)
[![CI](https://github.com/kadaliao/codex-safe-switch/actions/workflows/ci.yml/badge.svg)](https://github.com/kadaliao/codex-safe-switch/actions/workflows/ci.yml)

[中文](README.md) | English

One-key switch between [OpenAI Codex CLI](https://github.com/openai/codex) configurations — official ChatGPT login, third-party relays, multiple API keys, whatever. CLI + optional Alfred workflow.

Each profile owns the *provider* slice of `~/.codex/config.toml` (model, `[model_providers.*]`, auth method). A profile stores `auth.json` only when it really owns an auth file; relays that use `env_key` can rely on environment variables and leave the official ChatGPT login cache in place. Your local state (trusted projects, plugins, marketplaces, MCP servers, TUI prefs) is left untouched on every switch.

## Install

Requires [`uv`](https://github.com/astral-sh/uv).

```bash
uv tool install codex-safe-switch
```

This puts `codex-safe-switch` on `$PATH` (default `~/.local/bin/`). Run `uv tool update-shell` once if your shell can't find it.

Upgrade later with `uv tool upgrade codex-safe-switch`; uninstall with `uv tool uninstall codex-safe-switch`.

### No-install (one-off)

```bash
uvx --from codex-safe-switch codex-safe-switch ls
```

`uvx` resolves and caches an ephemeral environment per invocation. Convenient for trying it out, slower for hot paths like Alfred — use `uv tool install` if you want the workflow to feel snappy.

### Development version

Install directly from GitHub when you want the latest commit before it is released to PyPI:

```bash
uv tool install git+https://github.com/kadaliao/codex-safe-switch.git
```

### Alfred (optional)

After `uv tool install`, double-click `alfred/codex-safe-switch.alfredworkflow`. Trigger with keyword `cx`.

The workflow calls `$HOME/.local/bin/codex-safe-switch`; if `uv tool install` put the binary elsewhere (`uv tool dir --bin` to check), edit the two `script` blocks in the workflow's plist accordingly.

## CLI

```text
codex-safe-switch              # interactive picker (↑/↓, enter to switch, q to cancel)
codex-safe-switch ls           # list profiles, ★ marks the active one
codex-safe-switch current      # print the active profile
codex-safe-switch official     # switch back to official OpenAI ChatGPT login
codex-safe-switch openai       # alias of `official`
codex-safe-switch use [name]   # load <name>; omit for the picker
codex-safe-switch save <name>  # snapshot the current ~/.codex state as <name>
codex-safe-switch show <name>  # print <name>'s provider.toml + auth.json key names
codex-safe-switch state <name> # show/set the session-state scope for a profile
codex-safe-switch restart-codex
                           # terminate Codex app/server processes so config changes take effect
codex-safe-switch merge-history --dry-run
                           # preview history metadata changes without writing files
codex-safe-switch doctor-history
                           # inspect current history provider/model state read-only
codex-safe-switch rm <name>    # delete profile (the active one is protected)
codex-safe-switch alfred-list  # JSON for Alfred Script Filter
```

The picker auto-falls back to a numeric menu when stdin/stdout aren't TTYs (pipes, scripts).

## First run

If `~/.codex/profiles/` has no profiles yet, `codex-safe-switch` automatically imports the current
`~/.codex/config.toml` provider state the first time you run `codex-safe-switch`, `codex-safe-switch ls`,
or the Alfred workflow, and stores `auth.json` only when that profile owns its own auth.

- Official ChatGPT login is imported as the hidden `official` profile.
- Relay/API-key configs are imported as a regular profile named from `model_provider` (for example `relay`).
- If Codex has not been configured yet, the CLI explains that you need to configure Codex once or run
  `codex-safe-switch save <name>` after setting up the provider manually.

When you need the Codex desktop app or app server to pick up a switch immediately, use:

```bash
codex-safe-switch use <name> --restart-codex
codex-safe-switch official --restart-codex
codex-safe-switch restart-codex
```

The restart command terminates matching Codex app/server processes while avoiding `codex-safe-switch` itself.

## Official OpenAI shortcut

`codex-safe-switch official` is the one-step way back to the official OpenAI ChatGPT login.

- The tool keeps a hidden `~/.codex/profiles/.official/` snapshot for the official config/auth.
- The first time you switch away from an official OpenAI session, that snapshot is refreshed automatically.
- `codex-safe-switch openai` is an alias if you prefer typing the provider name directly.

## Shared history by default

After every `use` / `official` switch, `codex-safe-switch` automatically aligns local Codex history metadata to the active provider and model identity.

- You no longer need to remember `merge-history` during normal profile switching.
- This keeps session history visible when moving between relay profiles and the official OpenAI login, including surfaces that filter by model id.
- If `session_index.jsonl` has fallen behind the latest threads in SQLite, the switch appends repaired index entries from `state_5.sqlite` so mobile history lists do not stay pinned to an older point in time.
- If this host has used Codex remote-control before, the switch also checks the managed app-server path that desktop/mobile remote access depends on. It retries through the managed daemon when an old unmanaged unix app-server owns the socket; if Desktop still has old SSH remote proxy processes, it stops only those proxy processes so the next connection returns to the current Mac; and it prints the official standalone install command when that managed install is missing.
- `merge-history --keep-models` still exists if you want a provider-only repair and need to preserve historical per-thread model ids.
- `merge-history --dry-run` reports rollout files/lines, SQLite rows, and the backup path it would create without writing anything.
- `doctor-history` is read-only and summarizes the active profile, current provider/model, session-state mode, SQLite `threads` distribution, recent threads, planned alignment counts, and provider/model drift.

## Profile format

```text
~/.codex/profiles/
├── .active                       # plaintext: name of the active profile
├── chatgpt-official/
│   ├── auth.json                 # full file copied into ~/.codex/auth.json
│   └── provider.toml             # empty = use ChatGPT login
└── myrelay/
    ├── auth.json                 # optional; only needed when the profile owns a key/token
    └── provider.toml             # only provider-related keys (see examples/)
```

The following top-level keys + tables are owned by a profile (swapped on `use`); everything else in `~/.codex/config.toml` is preserved:

- `model`, `model_provider`, `model_reasoning_effort`, `model_reasoning_summary`, `model_verbosity`
- `wire_api`, `disable_response_storage`, `preferred_auth_method`
- `[model_providers.*]`

## Adding a relay profile

1. Configure the relay normally in `~/.codex/config.toml` and verify `codex` works.
2. For a relay whose key comes from the environment, prefer `requires_openai_auth = false` plus `env_key = "..."`. That profile does not need `auth.json`; switching to it preserves the current official ChatGPT login cache so Codex remote connections can keep using the same ChatGPT account.
3. `codex-safe-switch save <name>` — snapshots the provider slice into a new profile. It only stores `auth.json` when the provider explicitly needs OpenAI/ChatGPT auth, or for legacy API-key configs that do not declare `requires_openai_auth = false`.
4. `cx` in Alfred (or `codex-safe-switch use <name>`) to switch anytime.

`requires_openai_auth = false` only means the relay profile does not own `auth.json`. Mobile history sync still depends on a healthy official Codex remote-control/app-server install; copying tokens into each profile is not the durable fix.

Or build the files by hand — see `examples/relay-profile/`.

## Env overrides

| Var                  | Default              | Purpose                           |
| -------------------- | -------------------- | --------------------------------- |
| `CODEX_PROFILE_ROOT` | `~/.codex/profiles`  | where profiles live               |
| `CODEX_HOME`         | `~/.codex`           | the codex config dir to write     |

## Releasing

Packages are published on PyPI as [`codex-safe-switch`](https://pypi.org/project/codex-safe-switch/).

Current GitHub Actions triggers:

- Pushes to `main` and pull requests run CI: Python unit tests, `uv build`, and `twine check`.
- Pushing a `v*` tag runs the `Publish to PyPI` workflow. It verifies the tag matches `pyproject.toml`, builds the package, and publishes to PyPI.
- The Alfred workflow is not built by GitHub Actions today; the repository commits the ready-to-import `alfred/codex-safe-switch.alfredworkflow` file.

1. Update `version` in `pyproject.toml`.
2. Run `uv run python -m unittest tests.test_cli` and `uv build`.
3. Commit the version bump and push `main`.
4. Create and push a matching tag:

   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

The `Publish to PyPI` workflow verifies that the tag version matches `pyproject.toml`, runs tests, builds the wheel and sdist, checks the distributions, and publishes them to PyPI. Manual local publishing is still possible with `uvx twine upload dist/*` when needed.

## License

MIT
