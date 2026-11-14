# MPC Plugin SDK MCP server

Lets Claude Desktop (or any MCP client) run the whole MPC plugin workflow: scaffold, write, validate, build,
test in the host simulator, package and deploy, without a shell. It wraps the SDK's own scripts in `tools/`;
it never compiles anything itself and has no generic command tool.

## Install (Option A)

Needs Python 3.10+ for the server (the `mcp` package requires it). The SDK scripts still run on 3.9+.
Clone this repository into an `mcp/` folder inside a checkout of the
[MPC Plugin SDK](https://github.com/inmusic-software/mpc-plugin-builder-sdk), so the server finds the SDK
one level up:

```
git clone https://github.com/inmusic-software/mpc-plugin-builder-sdk.git
git clone https://github.com/fedec65/AkaiMCP.git mpc-plugin-builder-sdk/mcp
python3 mpc-plugin-builder-sdk/mcp/install_config.py --mpc-host 192.168.1.50
```

To keep the SDK somewhere else, set `SDK_ROOT` in the server's config entry instead.

The installer creates `mcp/.venv`, installs `mcp` and `jsonschema` into it, backs up
`claude_desktop_config.json`, and adds or updates only the `mpc-sdk` entry. It is safe to run again.
`--mpc-host` is optional (needed only for the `mpc_*` tools). Then quit and reopen Claude Desktop.

## Configuration (environment variables in the config entry)

| Variable | Default | Meaning |
|---|---|---|
| `SDK_ROOT` | parent of `mcp/` | SDK checkout |
| `WORKSPACE` | `SDK_ROOT` | writable folder for `plugins/` and `.mpc-mcp/` (job logs, deploy state) |
| `MPC_HOST` | unset | MPC IP for `mpc_list` / `mpc_install` / `mpc_remove` |
| `PYTHON` | the server's interpreter | interpreter for the SDK scripts |
| `MPC_SSH_PASSWORD` | unset | passed through only to `mpc_remote.py`, never logged |

## Tools

| Tool | Wraps | Notes |
|---|---|---|
| `sdk_info` | - | SDK git version, Pythons, Zig cache, host_sim, firmware requirements |
| `list_plugins` | `plugins/`, `examples/` | name, kind, version, built |
| `new_plugin(name, manufacturer, kind)` | `tools/new_plugin.py` | refuses an existing folder |
| `list_plugin_files` / `read_plugin_file` / `write_plugin_file` | filesystem | sandboxed; writes `.cpp .h .hpp .json .md` only, 512 KB max; examples and `build/` are read-only |
| `validate(plugin)` | `schemas/` + `tools/validate.py` | schema-checks the source; runs `validate.py` on the build output when it exists |
| `generate_ui(plugin)` | `tools/gen_ui.py` | needs a previous build |
| `build(plugin, targets)` | `tools/build.py` | background job; `targets` = `["arm32","arm64"]` (default), one of them, or `["native"]` |
| `test_host_sim(plugin, notes, seconds, playing)` | `.sdk-tools/host_sim ... test` | background job; verdict pass / FAIL / crash, writes `build/test.wav` |
| `package(plugin)` | `tools/package.py` | returns the zip path |
| `job_status(job_id, tail)` / `job_cancel(job_id)` | job runner | no `job_id` lists recent jobs |
| `mpc_list(drive)` | `tools/mpc_remote.py list` | read-only |
| `mpc_install(plugin, confirm, drive, replace)` | `mpc_remote.py add` / `update` | see deploy rules |
| `mpc_remove(name, confirm, drive)` | `mpc_remote.py remove` | project name or installed folder name |

Every tool returns `ok`, `exit_code`, `stdout`, `stderr` (20 KB max; the full text goes to
`.mpc-mcp/logs/`) and `paths` relative to the workspace, plus tool-specific fields.

**Resources:** `mpc-sdk://guide/overview`, `docs/{name}`, `schema/{plugin|ui}`, `framework/{file}`,
`example/{plugin}/{path}`, `skill/{new|dsp|ui|build|test|package|deploy|presets|troubleshoot}`. Missing files
are skipped. **Prompts:** `new_instrument`, `new_effect`, `fix_build_errors`, `port_open_source_dsp`,
`deploy_to_mpc`.

## Jobs

`build` and `test_host_sim` start a detached process group through `_job_wrapper.py` and return a `job_id`
at once. The log is in `.mpc-mcp/jobs/<id>.log` and the exit code in `<id>.exit`, so a job keeps running if
Claude disconnects, and `job_status` works after a reconnect or a server restart. At most 2 jobs run at once.

## Deploy rules

`mpc_install` refuses unless `confirm=true` **and** both a `validate` and a full (arm32 + arm64) build
succeeded on the current files: any source change after either one blocks the deploy until both are rerun.
Nothing deploys automatically. Without `replace=true` an installed copy is left alone. Results say what
changed on the device and how to recover (remove the plugin folder from `VST2 Plugins`).
`mpc_remote.py` runs non-interactively: if the MPC has several drives, pass `drive`.

## Tests

```
mcp/.venv/bin/pip install pytest
mcp/.venv/bin/python -m pytest mcp/tests
```

Covers the sandbox (`..`, absolute paths, symlink escapes, oversized files), the result shape on success,
failure and timeout, job survival across a manager restart, cancellation and the 2-job limit, deploy gating,
startup without `.claude/skills/`, and installer idempotence.

## Differences from the spec (the code won)

- `new_plugin` needs a `manufacturer` (the script requires `--vendor`, and AGENTS.md says to ask the user).
  Names follow the SDK's rules (spaces allowed, at most 32 characters), not `[A-Za-z][A-Za-z0-9_]{0,31}`.
- `tools/validate.py` checks the **built** folder, not the sources, so `validate` adds its own schema check.
- The server needs Python 3.10+, because `mcp` requires it. `jsonschema` (pure Python) is a second dependency.
- The host simulator can be scripted, so `test_host_sim` runs it rather than returning instructions.
- `mpc_remote.py` finds the MPC by the address you give it and uses SSH as `root`, with no password unless
  `MPC_SSH_PASSWORD` is set (the MPC's developer mode must be on).
- The SDK licence is MIT, not GNU. This server is AGPL-3.0 (see `LICENSE`); it calls the SDK's
  scripts and does not include SDK code.

## Proposed upstream patch (needed for Option C)

The scripts take a project path and write to `<project>/build`, so `WORKSPACE` works for projects. But
`tools/build.py` puts the Zig venv and `host_sim` in `SDK_ROOT/.sdk-tools`, which is read-only inside an
`.mcpb` bundle. Proposed change: honour an `SDK_TOOLS_DIR` environment variable in `build.py`. Option C is not
started yet.
