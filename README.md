# codex-safe-switch

[![PyPI](https://img.shields.io/pypi/v/codex-safe-switch.svg)](https://pypi.org/project/codex-safe-switch/)
[![CI](https://github.com/kadaliao/codex-safe-switch/actions/workflows/ci.yml/badge.svg)](https://github.com/kadaliao/codex-safe-switch/actions/workflows/ci.yml)

中文 | [English](README.en.md)

在官方 OpenAI provider、第三方 relay、多组 API key 之间一键切换 [Codex CLI](https://github.com/openai/codex) 的 provider 配置。CLI + 可选 Alfred workflow。

## 安装

```bash
uv tool install codex-safe-switch
```

需要先有 [`uv`](https://github.com/astral-sh/uv)。安装后 `codex-safe-switch` 会被放到 `$PATH`(默认 `~/.local/bin/`)。

## 快速开始

```bash
codex-safe-switch           # 交互选择(↑/↓,回车切换)
codex-safe-switch ls        # 列出所有 profile,★ 表示当前
codex-safe-switch save dev  # 把当前 ~/.codex 状态存成 dev
codex-safe-switch official  # 切回官方 OpenAI provider
```

首次运行会自动把当前 `~/.codex/config.toml` 导入成 profile,不会丢配置。

<details>
<summary><strong>完整命令</strong></summary>

```text
codex-safe-switch              # 交互式选择器
codex-safe-switch ls           # 列出 profiles,★ 表示当前 active
codex-safe-switch current      # 打印当前 active profile
codex-safe-switch official     # 切回官方 OpenAI provider(别名:openai)
codex-safe-switch use [name]   # 加载 <name>;不传 name 时进入选择器
codex-safe-switch save <name>  # 把当前 provider 配置保存成 <name>
codex-safe-switch show <name>  # 打印 <name> 的 provider.toml 和 session-state
codex-safe-switch state <name> # 查看/设置 profile 的 session-state 作用域
codex-safe-switch rm <name>    # 删除 profile(不允许删除 active)
codex-safe-switch restart-codex
                               # 终止 Codex app/server 进程,让配置立即生效
codex-safe-switch merge-history --dry-run
                               # 预览历史 metadata 修复,不写入文件
codex-safe-switch doctor-history
                               # 只读检查历史 provider/model 状态
codex-safe-switch alfred-list  # Alfred Script Filter JSON
```

`use` / `official` 都可以加 `--restart-codex`,顺手重启 Codex app/server。

当 stdin/stdout 不是 TTY(管道、脚本)时,选择器会自动降级成数字菜单。

</details>

<details>
<summary><strong>Alfred 工作流</strong></summary>

执行 `uv tool install` 后,双击 `alfred/codex-safe-switch.alfredworkflow` 导入 Alfred。触发关键词是 `cx`。

workflow 默认调用 `$HOME/.local/bin/codex-safe-switch`。如果你的 `uv tool install` 把命令装到了别处,可以用 `uv tool dir --bin` 查看路径,然后修改 workflow plist 里的两个 script block。

</details>

<details>
<summary><strong>它怎么保证"safe"切换</strong></summary>

**只接管 provider,不动本地状态。** 每个 profile 接管 `~/.codex/config.toml` 里的下面这些字段;其他内容(trusted projects、plugins、marketplaces、MCP servers、TUI 偏好等)切换时完整保留:

- `model`, `model_provider`, `model_reasoning_effort`, `model_reasoning_summary`, `model_verbosity`
- `wire_api`, `disable_response_storage`, `preferred_auth_method`
- `[model_providers.*]`

**不接管 `auth.json`。** Profile 只保存 provider 相关配置;`~/.codex/auth.json` 由 Codex 自己维护。`save` / `use` / `official` 都不会保存或写回 `auth.json`,所以切换 provider 不会覆盖你的官方 ChatGPT 登录缓存或本地认证状态。

**历史会话默认对齐。** 每次 `use` / `official` 后自动把本地历史 metadata 对齐到当前 provider 和 model,所以 relay 和官方账号之间切换时历史不会消失:

- 自动修复 rollout 文件 + `state_5.sqlite` 里的 provider/model 列。
- 如果 `session_index.jsonl` 落后于 SQLite 最新 thread,会补追加索引,避免移动端历史停在旧时间点。
- 已用过 Codex remote-control 的机器会顺手检查 managed app-server 链路,处理旧的 unix socket / SSH proxy 残留。
- `merge-history --keep-models` 可以只修 provider 不改 model;`--dry-run` 预览;`doctor-history` 只读诊断。

**官方 OpenAI 一键回退。** `codex-safe-switch official` 切回官方 OpenAI provider,工具维护隐藏 provider 快照 `~/.codex/profiles/.official/`,第一次从官方切走时自动刷新。

**进程隔离。** `restart-codex`(以及 `--restart-codex`)精确跳过 `codex-safe-switch` 自身进程,不会自杀。

</details>

<details>
<summary><strong>Profile 格式 + 添加 relay</strong></summary>

```text
~/.codex/profiles/
├── .active                       # 明文:当前 active profile 名
├── .official/
│   └── provider.toml             # 官方 OpenAI provider 片段
└── myrelay/
    └── provider.toml             # 只包含 provider 字段(见 examples/)
```

**添加 relay profile**

1. 在 `~/.codex/config.toml` 里配置 relay,确认 `codex` 能跑。
2. 如果 key 来自环境变量,在 provider 里配置 `env_key = "..."`;profile 不需要也不会保存 `auth.json`。
3. `codex-safe-switch save <name>` 把 provider 片段存成 profile。
4. 之后用 `cx`(Alfred)或 `codex-safe-switch use <name>` 随时切。

也可以手写 profile 文件,参考 `examples/relay-profile/`。

</details>

<details>
<summary><strong>环境变量 / 开发版 / 发版</strong></summary>

**环境变量**

| 变量                 | 默认值              | 用途                    |
| -------------------- | ------------------ | ----------------------- |
| `CODEX_PROFILE_ROOT` | `~/.codex/profiles` | profiles 存放位置        |
| `CODEX_HOME`         | `~/.codex`          | 要写入的 Codex 配置目录   |

**免安装试用**

```bash
uvx --from codex-safe-switch codex-safe-switch ls
```

**装开发版**

```bash
uv tool install git+https://github.com/kadaliao/codex-safe-switch.git
```

**发版**

push `v*` tag 触发 `Publish to PyPI` workflow,会校验 tag 版本和 `pyproject.toml` 一致,跑测试 + build + twine check 后发布。

```bash
git tag vX.Y.Z && git push origin vX.Y.Z
```

</details>

## License

MIT
