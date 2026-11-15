# AkaiMCP: build MPC plugins from Claude Desktop

Describe an instrument or effect in plain language in Claude Desktop, and get a plugin that runs on your
Akai MPC. This is an MCP server for the open-source
[MPC Plugin SDK](https://github.com/inmusic-software/mpc-plugin-builder-sdk) by inMusic. It gives Claude
Desktop the same build loop that Claude Code gets from the SDK: scaffold, write the code, validate, build,
test, package and install on the MPC. You don't need a terminal or any C++ knowledge.

> **You need the SDK too.** This repository contains only the server. The SDK does the real work (templates,
> compiler setup, build and test scripts), so you clone both. Setup takes about 10 minutes.

## How it works

```
You, in Claude Desktop:  "Make a tape delay with wow and flutter"
        │
        ▼
Claude writes the C++ code, the UI layout and the README
        │  calls tools such as new_plugin, write_plugin_file, build
        ▼
AkaiMCP server  (this repo, running on your computer, started by Claude Desktop)
        │  runs the SDK's own scripts, nothing else
        ▼
MPC Plugin SDK  (tools/new_plugin.py, build.py, validate.py, package.py, mpc_remote.py, host simulator)
        │
        ▼
plugins/Tape Delay/build/"<You> - VST - Tape Delay"/   ──►  USB stick / SD card, or straight to the MPC over Wi-Fi
```

- **Claude writes the plugin; the server runs the SDK.** The server has no build logic of its own. Each tool
  calls one fixed SDK script, and there is no tool that runs arbitrary commands.
- **Everything runs on your computer.** Builds use your machine, and installing reaches the MPC over your
  local network. Nothing is uploaded anywhere.
- **Builds run in the background.** The first build downloads the Zig cross compiler (about 50 MB) and
  compiles for both MPC processor families, which takes a few minutes. Claude starts the build, then checks on
  it until it finishes.
- **Claude gets the SDK's guidance.** Claude Desktop doesn't read the SDK's `AGENTS.md` and skills on its own,
  so the server provides them, plus the docs, schemas and example plugins, as MCP resources.
- **Nothing reaches your MPC without your yes.** Claude must ask you, and the server refuses to install
  unless the current files have passed validation and a full build.

## What you need

| | Notes |
|---|---|
| **Claude Desktop** | macOS or Windows |
| **Python 3.10 or newer** | Check with `python3 --version`. Get it from python.org, or on macOS `brew install python` |
| **git** | To clone the two repositories (or download them as zip files and unpack them the same way) |
| **The MPC Plugin SDK** | Cloned in step 1 below. Nothing else to install: it fetches its compiler on the first build |
| **Internet, once** | For the compiler download on the first build |
| **An MPC on MPC OS 3.10.1+** | MPC X, X SE, Live, Live II, Live III, One, One+, Key 61, Key 37, XL or Force. Needed only to play the result |

## Setup

### 1. Get the SDK

```bash
git clone https://github.com/inmusic-software/mpc-plugin-builder-sdk.git
```

Put it somewhere permanent: Claude Desktop starts the server from this folder every time.

### 2. Put this server inside the SDK, in a folder called `mcp`

```bash
git clone https://github.com/fedec65/AkaiMCP.git mpc-plugin-builder-sdk/mcp
```

The folder name matters: the server looks for the SDK one level up from itself. To keep the SDK somewhere
else, set `SDK_ROOT` instead (see [Configuration](#configuration)).

### 3. Run the installer

```bash
python3 mpc-plugin-builder-sdk/mcp/install_config.py
```

On Windows, use `py` instead of `python3`. The installer:

1. creates a private Python environment in `mcp/.venv` and installs the server's two dependencies (`mcp`
   and `jsonschema`) into it, so nothing is installed system-wide;
2. backs up your Claude Desktop config file (`claude_desktop_config.json`) and adds an `mpc-sdk` entry to it.
   Your other MCP servers and settings are left alone. Running it again is safe.

If you already know your MPC's IP address, add `--mpc-host 192.168.1.50`. You can also run the installer
again later to add it.

### 4. Restart Claude Desktop

Quit Claude Desktop completely and open it again. `mpc-sdk` now appears in its list of connected tools.
To check that it works, ask: *"Use mpc-sdk to show the SDK info."*

### 5. (Optional) Let Claude install plugins on your MPC over the network

Without this step you copy plugins over by USB stick or SD card, as described below. To have Claude
install them directly:

1. On the MPC (MPC OS 3.10.1 or later), open **Preferences → Enhancements** and switch on
   **Developer Panel**. This switches on **developer mode**, which starts the MPC's SSH server.
2. Find the MPC's IP address in **Preferences**: on the Wi-Fi tab (hold Shift to see it) or the Ethernet tab.
3. Put the MPC and your computer on the same network, then run the installer with the address and restart
   Claude Desktop:
   ```bash
   python3 mpc-plugin-builder-sdk/mcp/install_config.py --mpc-host 192.168.1.50
   ```

Developer mode opens a login on the device. Switch it off when you're done if the network isn't yours.

## Making your first plugin

Start a chat in Claude Desktop and describe what you want. You can also pick one of the server's prompts
(`new_instrument` or `new_effect`) from Claude Desktop's prompt menu. For example:

> Make an MPC effect called "Tape Echo": a tempo-synced delay with feedback, a feedback filter, wow and flutter,
> and a ping-pong switch. My manufacturer name is "Acme Audio".

Claude will:

1. ask for your manufacturer or brand name if you didn't give it. It becomes part of the plugin's permanent
   identity on the MPC;
2. create the project in `plugins/Tape Echo/`, write the code, the touch-screen layout and a README;
3. validate it, build it, fix any errors and rebuild until it's clean;
4. run the SDK's host simulator, a test host that loads the plugin the way the MPC does, and fix any failures;
5. tell you where the finished folder is, and offer to zip it or install it on your MPC.

### Getting it onto the MPC

The finished plugin is a folder such as `plugins/Tape Echo/build/Acme Audio - VST - Tape Echo/`.

- **By USB stick or SD card:** copy that folder into a folder named exactly `VST2 Plugins` at the root of the
  drive (create it if needed), then put the drive in the MPC.
- **Over the network** (after setup step 5): say *"install it on my MPC"*. Claude explains the risk, asks you
  to confirm, then installs it.

Then, on the MPC:

1. Turn on **Preferences → Enhancements (Beta) → Enable Enhancements** and **External Plugin Support**.
2. **Restart the MPC.** It only looks for new plugins when it starts. Save your project first.
3. The plugin appears in the plugin browser under **External Plugins**. Effects are added as an insert;
   instruments through a Plugin program.

After you change a plugin, rebuild and reinstall it, then remove and re-add it on the track, or restart the
MPC if the screen layout changed.

> **Safety.** Third-party plugins run inside the MPC software without protection. A bug can freeze or restart
> the MPC, and inMusic does not support third-party plugins. Test in the host simulator first (Claude does
> this) and save your work often. To recover from a bad plugin, delete its folder from `VST2 Plugins` and
> restart the MPC.

## Where things go

```
mpc-plugin-builder-sdk/            the SDK (from inMusic)
├── tools/  framework/  docs/ ...  SDK scripts and docs, used as they are
├── examples/                      StereoDelay, SimpleSynth, DrumSynth (read-only for Claude)
├── plugins/                       YOUR plugins: one folder per project
│   └── Tape Echo/
│       ├── plugin.json  ui.json  README.md  src/TapeEcho.cpp
│       ├── build/                 build output: the folder to copy to the MPC, plus test.wav
│       └── dist/                  zip files made by "package"
├── .sdk-tools/                    compiler and host simulator, downloaded/built on first use
├── .mpc-mcp/                      server state: background job logs, deploy checks
└── mcp/                           THIS repository
```

`plugins/`, `build/`, `.sdk-tools/` and `.mpc-mcp/` are ignored by git.

## Updating

```bash
git -C mpc-plugin-builder-sdk pull
git -C mpc-plugin-builder-sdk/mcp pull
python3 mpc-plugin-builder-sdk/mcp/install_config.py
```

Then restart Claude Desktop. The installer keeps your MPC address.

## Troubleshooting

| Problem | What to do |
|---|---|
| `mpc-sdk` doesn't appear in Claude Desktop | Quit Claude Desktop completely (not just close the window) and reopen it. Check that the paths in the `mpc-sdk` entry of `claude_desktop_config.json` exist. Claude Desktop's MCP logs show the server's startup errors |
| Installer says Python is too old | Install Python 3.10+ and run the installer with it, e.g. `python3.12 .../install_config.py` |
| The first build takes a long time | Normal: it downloads the compiler (about 50 MB) and builds for two processors. Later builds are faster |
| Build fails with a network or pip error | The first build needs internet to fetch the compiler. Check your connection or proxy, then build again |
| "MPC_HOST is not set" | Run the installer with `--mpc-host <ip>` and restart Claude Desktop, or use a USB stick |
| "Nothing answered at that address" | The MPC is off, on another network, or the IP is wrong. Some guest Wi-Fi networks block devices from reaching each other |
| "SSH server is off" / connection refused | Developer mode isn't on: see setup step 5 |
| "Several drives found" | Tell Claude which drive to use, e.g. "use the SD card" |
| Install refused: "files changed after the last successful build" | Working as intended: Claude must rebuild and validate the current files first |
| Installed, but the plugin doesn't show on the MPC | Restart the MPC, and check both Enhancements switches and MPC OS 3.10.1+ |

## Reference

### Configuration

Set these in the `env` block of the `mpc-sdk` entry in `claude_desktop_config.json`, then restart Claude
Desktop. The installer writes `MPC_HOST` (with `--mpc-host`) and `WORKSPACE` (with `--workspace`).

| Variable | Default | Meaning |
|---|---|---|
| `SDK_ROOT` | the folder above `mcp/` | where the SDK is |
| `WORKSPACE` | `SDK_ROOT` | writable folder for `plugins/` and `.mpc-mcp/` |
| `MPC_HOST` | unset | the MPC's IP address, for the `mpc_*` tools |
| `PYTHON` | the server's own Python | interpreter used to run the SDK scripts |
| `MPC_SSH_PASSWORD` | unset | only if you set an SSH password on the MPC; passed only to `mpc_remote.py` and never logged |

### Tools

| Tool | Runs | Notes |
|---|---|---|
| `sdk_info` | - | SDK version, Python, compiler cache, host simulator, MPC requirements |
| `list_plugins` | - | your plugins and the SDK examples: kind, version, whether built |
| `new_plugin(name, manufacturer, kind)` | `tools/new_plugin.py` | creates `plugins/<name>`; refuses if it exists |
| `list_plugin_files`, `read_plugin_file`, `write_plugin_file` | - | limited to the plugin's folder; writes `.cpp .h .hpp .json .md` only, 512 KB max; `build/` and the examples are read-only |
| `validate(plugin)` | `schemas/`, `tools/validate.py` | checks `plugin.json` and `ui.json` against the schemas, and the build output if there is one |
| `generate_ui(plugin)` | `tools/gen_ui.py` | redo the touch-screen layout without recompiling (needs a previous build) |
| `build(plugin, targets)` | `tools/build.py` | background job; both MPC processors by default. `["arm32"]` or `["arm64"]` alone is a quick compile check: it skips the SDK validator and can't be installed. Refused if `file32`/`file64` in `plugin.json` point outside the plugin folder, or if a build of the same plugin is already running |
| `test_host_sim(plugin, notes, seconds, playing)` | the SDK host simulator | background job; result pass / FAIL / crash, and `build/test.wav` |
| `package(plugin)` | `tools/package.py` | zip with install instructions, for sharing |
| `job_status(job_id, tail)`, `job_cancel(job_id)` | - | progress and log of a background job; no id lists recent jobs |
| `mpc_list(drive)` | `tools/mpc_remote.py list` | what's installed on the MPC (read-only) |
| `mpc_install(plugin, confirm, drive, replace)` | `tools/mpc_remote.py add/update` | only after your yes; see the rules below |
| `mpc_remove(name, confirm, drive)` | `tools/mpc_remote.py remove` | only after your yes |

Every tool returns `ok`, `exit_code`, `stdout`, `stderr` and `paths` (relative to the workspace). Output is
capped at 20 KB; longer output is saved in full under `.mpc-mcp/logs/`.

**Resources:** `mpc-sdk://guide/overview` (start here), `mpc-sdk://docs/{name}`,
`mpc-sdk://schema/{plugin|ui}`, `mpc-sdk://framework/{file}`, `mpc-sdk://example/{plugin}/{path}`,
`mpc-sdk://skill/{new|dsp|ui|build|test|package|deploy|presets|troubleshoot}`.
Files missing from your SDK version are left out.

**Prompts:** `new_instrument`, `new_effect`, `fix_build_errors`, `port_open_source_dsp` (asks Claude to check
the source's licence and credit its author), `deploy_to_mpc` (a step-by-step safe install checklist).

### Install rules

- `mpc_install` is refused unless `confirm=true` **and** `validate` passed on the current files **and** the
  most recent build attempt is a finished, successful full build (both MPC processors) of those same files.
  Any edit after either one blocks the install until both run again.
- A newer build that failed, was cancelled, is still running or built only one processor blocks the install,
  even if an earlier build succeeded: it may have left the output folder half-written.
- After `generate_ui` rewrites the output folder, run `validate` again before installing.
- Only the current output folder is sent to the MPC. Older folders left in `build/` (after renaming the plugin
  or changing the manufacturer) are never uploaded.
- Nothing installs automatically after a build.
- An already installed copy is replaced only with `replace=true`.
- Results say what changed on the MPC and how to recover.

### Background jobs

`build` and `test_host_sim` run as separate processes and return a job id at once. Each job logs to
`.mpc-mcp/jobs/<id>.log` and records its exit code when it finishes. A job keeps running if Claude Desktop
disconnects or restarts, and `job_status` still works afterwards. At most 2 jobs run at a time.
`job_cancel` stops the job's whole process group: it waits for every process to end and force-kills any that
ignore the stop signal.

## Development

```bash
mpc-plugin-builder-sdk/mcp/.venv/bin/pip install pytest
cd mpc-plugin-builder-sdk/mcp && .venv/bin/python -m pytest tests
```

The tests use a temporary copy of the SDK and need neither a compiler nor an MPC. They cover the path checks
(`..`, absolute paths, symlinks leaving the folder, oversized files), the result shape on success, failure and
timeout, background jobs surviving a restart, cancellation and the 2-job limit, the install rules, startup
without the SDK skills folder, and the installer leaving other config entries alone.

### Notes for maintainers

Where the implementation differs from the original spec, because the SDK code works differently:

- `new_plugin` takes a `manufacturer`: the SDK script requires one, and the SDK's `AGENTS.md` says to ask the
  user. Names follow the SDK's rules (spaces allowed, at most 32 characters).
- `tools/validate.py` checks the built folder, not the sources, so `validate` adds a schema check of the sources.
- The server needs Python 3.10+, because the `mcp` package requires it. The SDK scripts still run on 3.9+.
- The host simulator runs without user input, so `test_host_sim` runs it rather than returning instructions.
- `mpc_remote.py` connects to the address it is given over SSH as `root`, with no password unless
  `MPC_SSH_PASSWORD` is set.
- There is no tool to restart the MPC app yet; users restart the MPC themselves.

**Needed before a one-click `.mcpb` bundle (Option C):** the SDK scripts write to the project folder, so
`WORKSPACE` works for projects. But `tools/build.py` always puts the compiler and host simulator in
`SDK_ROOT/.sdk-tools`, which is read-only inside a bundle. The proposed upstream fix is to let `build.py`
read an `SDK_TOOLS_DIR` environment variable.

## Licence

This server is AGPL-3.0 (see `LICENSE`). It runs the SDK's scripts and includes no SDK code. The MPC Plugin
SDK is MIT-licensed by inMusic Brands, Inc. "MPC" and "Akai Professional" are trademarks of inMusic Brands,
Inc.; "VST" is a trademark of Steinberg Media Technologies GmbH. This project is not affiliated with or
endorsed by inMusic.
