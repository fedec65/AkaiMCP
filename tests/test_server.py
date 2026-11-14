"""Unit and integration tests for the MPC SDK MCP server. Run:  mcp/.venv/bin/python -m pytest mcp/tests

They use a temporary WORKSPACE and a temporary copy of the SDK's text files, so nothing in the real
checkout is changed and no compiler or MPC is needed.
"""

import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

MCP_DIR = Path(__file__).resolve().parents[1]
REAL_SDK = MCP_DIR.parent
sys.path.insert(0, str(MCP_DIR))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A fake SDK root (real tools/templates/schemas, no skills) plus an empty workspace."""
    sdk = tmp_path / "sdk"
    for d in ("tools", "templates", "schemas", "docs", "framework", "examples"):
        shutil.copytree(REAL_SDK / d, sdk / d)
    for f in ("AGENTS.md", "GETTING_STARTED.md"):
        shutil.copy2(REAL_SDK / f, sdk / f)
    ws = tmp_path / "ws"
    (ws / "plugins").mkdir(parents=True)
    monkeypatch.setenv("SDK_ROOT", str(sdk))
    monkeypatch.setenv("WORKSPACE", str(ws))
    monkeypatch.delenv("MPC_HOST", raising=False)
    import sdk_paths
    import server
    server._jobs = None
    return {"sdk": sdk, "ws": ws, "sp": sdk_paths, "server": server}


def run(coro):
    return asyncio.run(coro) if asyncio.iscoroutine(coro) else coro


def make_plugin(env, name="Tape Echo", kind="effect"):
    r = run(env["server"].new_plugin(name, "Test Vendor", kind))
    assert r["ok"], r
    return env["ws"] / "plugins" / name


# --- path sandbox ----------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["../x.cpp", "src/../../x.cpp", "/etc/passwd", "C:/x.cpp", "a\x00b.cpp",
                                 "src\\x.cpp", ""])
def test_sandbox_rejects_bad_paths(env, bad):
    project = make_plugin(env)
    with pytest.raises(env["sp"].SandboxError):
        env["sp"].resolve_in_plugin(project, bad)


@pytest.mark.parametrize("bad", ["..", ".hidden", "a/b", "a\\b", "x:y", "", "a\x00", "q?"])
def test_sandbox_rejects_bad_plugin_names(env, bad):
    with pytest.raises(env["sp"].SandboxError):
        env["sp"].check_plugin_name(bad)


def test_symlink_escape_rejected(env, tmp_path):
    project = make_plugin(env)
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / "link").symlink_to(outside, target_is_directory=True)
    r = env["server"].write_plugin_file("Tape Echo", "link/evil.cpp", "int x;")
    assert not r["ok"] and "leaves" in r["message"]
    assert not (outside / "evil.cpp").exists()
    (env["ws"] / "plugins" / "Escape").symlink_to(outside, target_is_directory=True)
    r = env["server"].list_plugin_files("Escape")
    assert not r["ok"]


def test_write_limits(env):
    make_plugin(env)
    s = env["server"]
    assert not s.write_plugin_file("Tape Echo", "src/big.cpp", "x" * (512 * 1024 + 1))["ok"]
    assert not s.write_plugin_file("Tape Echo", "run.sh", "echo hi")["ok"]
    assert not s.write_plugin_file("Tape Echo", "build/x.json", "{}")["ok"]
    assert not s.write_plugin_file("StereoDelay", "README.md", "x")["ok"]  # examples are read-only
    r = s.write_plugin_file("Tape Echo", "src/dsp/filter.h", "#pragma once\n")
    assert r["ok"] and (env["ws"] / "plugins" / "Tape Echo" / "src" / "dsp" / "filter.h").is_file()


# --- result shape ----------------------------------------------------------------------------

KEYS = {"ok", "exit_code", "stdout", "stderr", "paths"}


def test_result_shape_on_success_failure_and_timeout(env, monkeypatch):
    s = env["server"]
    make_plugin(env)
    for r in (s.sdk_info(), s.list_plugins(), s.list_plugin_files("Tape Echo"),
              s.read_plugin_file("Tape Echo", "nope.cpp"), s.read_plugin_file("Nope", "x"),
              run(s.new_plugin("Tape Echo", "Test Vendor", "effect")),  # exists -> refused
              run(s.package("Tape Echo")), s.test_host_sim("Tape Echo"), s.job_status("nope-1"),
              run(s.mpc_list()), run(s.mpc_install("Tape Echo", confirm=True)), run(s.validate("Tape Echo"))):
        assert KEYS <= set(r), r
    # script failure: run a real SDK script with arguments it rejects
    r = s.run_sdk([sys.executable, s.script("package.py"), str(env["ws"] / "missing")], "package")
    assert KEYS <= set(r) and not r["ok"] and r["exit_code"] == 1
    # timeout
    r = s.run_sdk([sys.executable, "-c", "import time; time.sleep(5)"], "sleep", timeout=1)
    assert KEYS <= set(r) and not r["ok"] and "timed out" in r["message"]


def test_output_trimmed(env):
    s = env["server"]
    big = s.trim("x" * 100000, "big")
    assert len(big.encode()) < 21 * 1024 and "trimmed" in big


# --- workflow --------------------------------------------------------------------------------

def test_new_plugin_validate_and_list(env):
    s = env["server"]
    project = make_plugin(env, "Dune Bass", "instrument")
    assert (project / "plugin.json").is_file()
    r = run(s.validate("Dune Bass"))
    assert r["ok"], r
    names = [p["plugin"] for p in s.list_plugins()["plugins"]]
    assert "Dune Bass" in names and "StereoDelay" in names
    s.write_plugin_file("Dune Bass", "plugin.json", json.dumps({"name": "Dune Bass"}))
    r = run(s.validate("Dune Bass"))
    assert not r["ok"] and r["schema_problems"]


def test_server_starts_without_skills(env):
    assert not (env["sdk"] / ".claude").exists()
    n = env["server"].register_resources()
    assert n > 10


# --- jobs ------------------------------------------------------------------------------------

def wait_state(jm, job_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = jm.state(jm.record(job_id))
        if st["state"] != "running":
            return st
        time.sleep(0.1)
    return jm.state(jm.record(job_id))


def test_job_survives_manager_restart_and_cancel(env, tmp_path):
    from jobs import JobManager, JobLimitError
    jdir = tmp_path / "jobs"
    jm = JobManager(jdir)
    rec = jm.start("t", [sys.executable, "-c", "import time; print('hi', flush=True); time.sleep(1)"],
                   str(tmp_path), dict(os.environ))
    jm2 = JobManager(jdir)  # a fresh server after the client reconnects
    assert jm2.state(jm2.record(rec["id"]))["state"] == "running"
    assert wait_state(jm2, rec["id"])["state"] == "succeeded"
    assert "hi" in jm2.log_tail(rec["id"], 10, 1000)
    long = [sys.executable, "-c", "import time; time.sleep(60)"]
    a = jm.start("t", long, str(tmp_path), dict(os.environ))
    b = jm.start("t", long, str(tmp_path), dict(os.environ))
    with pytest.raises(JobLimitError):
        jm.start("t", long, str(tmp_path), dict(os.environ))
    assert jm.cancel(a["id"])["state"] == "cancelled"
    assert jm2.cancel(b["id"])["state"] == "cancelled"


# --- deploy gating ---------------------------------------------------------------------------

def fake_build(env, project, code=0, target="all", wait=True):
    """Record a build job the way server.build does, running a stand-in command instead of build.py."""
    s = env["server"]
    (s.project_paths(project)["out_dir"] / "VST").mkdir(parents=True, exist_ok=True)
    gen = s.bump_output_gen(project)
    cmd = "import sys, time; time.sleep(30)" if not wait else "import sys; sys.exit(%d)" % code
    rec = s.jobs().start("build", [sys.executable, "-c", cmd], str(project), dict(os.environ),
                         meta={"project": str(project), "plugin": project.name, "target": target,
                               "fingerprint": env["sp"].fingerprint(project), "output_gen": gen})
    if wait:
        wait_state(s.jobs(), rec["id"])
    return rec


def mark_validated(env, project, built=False):
    s = env["server"]
    st = s.load_state()
    st.setdefault("validate", {})[str(project)] = {"fingerprint": env["sp"].fingerprint(project),
                                                   "built": built, "output_gen": s.output_gen(project)}
    s.save_state(st)


def test_deploy_gate(env, monkeypatch):
    s = env["server"]
    project = make_plugin(env)
    monkeypatch.setenv("MPC_HOST", "192.0.2.1")
    r = run(s.mpc_install("Tape Echo"))
    assert not r["ok"] and "confirm" in r["message"]
    r = run(s.mpc_install("Tape Echo", confirm=True))
    assert not r["ok"] and "validate" in r["message"]

    # Simulate a finished successful full build and a passing validate on the current files.
    fake_build(env, project)
    mark_validated(env, project)
    assert s.deploy_gate(project) is None

    time.sleep(0.01)
    s.write_plugin_file("Tape Echo", "README.md", "changed after the build\n")
    reason = s.deploy_gate(project)
    assert reason and "validate" in reason
    st = s.load_state()
    st["validate"][str(project)]["fingerprint"] = env["sp"].fingerprint(project)
    s.save_state(st)
    reason = s.deploy_gate(project)
    assert reason and "changed after the last successful build" in reason
    r = run(s.mpc_install("Tape Echo", confirm=True))
    assert not r["ok"] and "Rebuild" in r["message"]


def test_gate_follows_latest_build_attempt(env):
    """Devin #2: a newer failed, cancelled, single-target or running build closes the gate."""
    s = env["server"]
    project = make_plugin(env)
    fake_build(env, project)
    mark_validated(env, project)
    assert s.deploy_gate(project) is None

    fake_build(env, project, code=1)  # same files, but the rebuild failed half-way
    assert "failed" in s.deploy_gate(project)

    fake_build(env, project, target="arm32")
    assert "single-target" in s.deploy_gate(project)

    running = fake_build(env, project, wait=False)
    assert "still running" in s.deploy_gate(project)
    r = s.build("Tape Echo")
    assert not r["ok"] and "already running" in r["message"]
    s.jobs().cancel(running["id"])
    assert "cancelled" in s.deploy_gate(project)

    fake_build(env, project)
    assert s.deploy_gate(project) is None


def test_generate_ui_after_build_needs_revalidate(env):
    s = env["server"]
    project = make_plugin(env)
    fake_build(env, project)
    mark_validated(env, project)
    assert s.deploy_gate(project) is None
    s.bump_output_gen(project)  # what generate_ui does before rewriting Plugin Skins
    assert "validate" in s.deploy_gate(project)
    mark_validated(env, project, built=True)  # validate.py ran on the rewritten output
    assert s.deploy_gate(project) is None


@pytest.mark.parametrize("bad", ["../../other/evil.so", "/tmp/evil.so", "C:/evil.so", "VST/../../x.so",
                                 "VST\\..\\..\\x.so", "VST/x.dll"])
def test_build_rejects_escaping_binary_paths(env, bad):
    """Devin #5: build.py compiles straight to file32/file64, so they must stay in the output folder."""
    s = env["server"]
    project = make_plugin(env)
    m = json.loads((project / "plugin.json").read_text())
    m["file32"] = bad
    s.write_plugin_file("Tape Echo", "plugin.json", json.dumps(m))
    r = s.build("Tape Echo")
    assert not r["ok"] and "file32" in r["message"] and "job_id" not in r
    assert any("file32" in p for p in run(s.validate("Tape Echo"))["schema_problems"])


def test_install_sends_only_current_output_folder(env, monkeypatch):
    """Devin #1: stale folders left in build/ by an older name must not be uploaded."""
    s = env["server"]
    project = make_plugin(env)
    (project / "build" / "Old Vendor - VST - Tape Echo" / "VST").mkdir(parents=True)
    fake_build(env, project)
    mark_validated(env, project)
    monkeypatch.setenv("MPC_HOST", "192.0.2.1")
    seen = {}

    async def fake_run(argv, *a, **k):
        seen["argv"] = argv
        return s.result(True, 0, stdout="installed")
    monkeypatch.setattr(s, "run_sdk_async", fake_run)
    r = run(s.mpc_install("Tape Echo", confirm=True))
    assert r["ok"], r
    out_dir = str(s.project_paths(project)["out_dir"])
    assert seen["argv"][4] == out_dir
    assert str(project) not in seen["argv"] and "Old Vendor" not in " ".join(seen["argv"])


def test_single_target_build_skips_sdk_validation(env, monkeypatch):
    """Devin #4: the SDK validator always fails a one-architecture build, so it is skipped there."""
    s = env["server"]
    make_plugin(env)
    calls = []
    monkeypatch.setattr(s.jobs(), "start", lambda kind, argv, cwd, e, meta=None: calls.append(argv) or
                        {"id": "x-%d" % len(calls), **(meta or {})})
    s.build("Tape Echo", ["arm64"])
    s.build("Tape Echo")
    assert "--no-validate" in calls[0] and "--no-validate" not in calls[1]


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_cancel_kills_children_that_ignore_sigterm(tmp_path):
    """Devin #3: the whole process group must be gone after job_cancel, not just the wrapper."""
    import signal
    from jobs import JobManager
    jm = JobManager(tmp_path / "jobs")
    marker = tmp_path / "ready"
    code = ("import signal, time, pathlib; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "pathlib.Path(%r).write_text('1'); time.sleep(60)" % str(marker))
    rec = jm.start("t", [sys.executable, "-c", code], str(tmp_path), dict(os.environ))
    deadline = time.time() + 10
    while not marker.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert marker.exists()
    assert jm.cancel(rec["id"])["state"] == "cancelled"
    with pytest.raises(ProcessLookupError):
        os.killpg(rec["pid"], 0)


def test_mpc_tools_need_host(env):
    r = run(env["server"].mpc_list())
    assert not r["ok"] and "MPC_HOST" in r["message"]
    r = run(env["server"].mpc_remove("Acme - VST - X"))
    assert not r["ok"] and "confirm" in r["message"]


# --- config installer ------------------------------------------------------------------------

def test_install_config_idempotent_and_preserves_others(tmp_path):
    import install_config as ic
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x", "args": ["y"]}}, "theme": "dark"}))
    assert ic.main(["--no-venv", "--config", str(cfg), "--mpc-host", "10.0.0.5"]) == 0
    data = json.loads(cfg.read_text())
    assert data["mcpServers"]["other"] == {"command": "x", "args": ["y"]} and data["theme"] == "dark"
    entry = data["mcpServers"]["mpc-sdk"]
    assert entry["args"] == [str(MCP_DIR / "server.py")] and entry["env"]["MPC_HOST"] == "10.0.0.5"
    before = cfg.read_text()
    backups = len(list(tmp_path.glob("*.bak-*")))
    assert ic.main(["--no-venv", "--config", str(cfg)]) == 0  # second run: keeps MPC_HOST, no change
    assert cfg.read_text() == before and len(list(tmp_path.glob("*.bak-*"))) == backups
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(SystemExit):
        ic.main(["--no-venv", "--config", str(bad)])
    assert bad.read_text() == "{not json"
