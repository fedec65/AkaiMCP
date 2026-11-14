"""Background job runner: build, host-sim test and other slow SDK steps.

Each job runs in its own process group through _job_wrapper.py, logs to
WORKSPACE/.mpc-mcp/jobs/<id>.log and records its exit code in <id>.exit. Job records persist on
disk, so status survives a server restart (older jobs are read-only history).
Standard library only.
"""

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

IS_WINDOWS = os.name == "nt"
MAX_CONCURRENT = 2
WRAPPER = Path(__file__).resolve().parent / "_job_wrapper.py"


class JobLimitError(RuntimeError):
    pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WINDOWS:
        try:
            out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"], capture_output=True, text=True,
                                 timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return str(pid) in out
    try:
        # Reap it if it is our own exited child; otherwise a zombie would look alive forever.
        done, _status = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return False
    except ChildProcessError:
        pass
    except OSError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class JobManager:
    def __init__(self, job_dir: Path):
        self.job_dir = Path(job_dir)
        self._procs: Dict[str, subprocess.Popen] = {}

    # -- records -------------------------------------------------------------------------------
    def _path(self, job_id: str, ext: str) -> Path:
        if not job_id or not all(c.isalnum() or c == "-" for c in job_id):
            raise ValueError("invalid job id")
        return self.job_dir / (job_id + ext)

    def _write_record(self, rec: dict) -> None:
        p = self._path(rec["id"], ".json")
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rec, indent=2), encoding="utf-8")
        os.replace(tmp, p)

    def record(self, job_id: str) -> Optional[dict]:
        p = self._path(job_id, ".json")
        if not p.is_file():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def all_records(self) -> List[dict]:
        if not self.job_dir.is_dir():
            return []
        out = []
        for p in self.job_dir.glob("*.json"):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        out.sort(key=lambda r: r.get("started_at", 0))
        return out

    # -- state ---------------------------------------------------------------------------------
    def state(self, rec: dict) -> dict:
        """Returns {'state': running|succeeded|failed|cancelled|lost, 'exit_code': int|None}."""
        job_id = rec["id"]
        exit_file = self._path(job_id, ".exit")
        proc = self._procs.get(job_id)
        if proc is not None and proc.poll() is None:
            return {"state": "running", "exit_code": None}
        if exit_file.is_file():
            try:
                code = int(exit_file.read_text().strip())
            except ValueError:
                code = -1
            if rec.get("cancelled"):
                return {"state": "cancelled", "exit_code": code}
            return {"state": "succeeded" if code == 0 else "failed", "exit_code": code}
        if proc is None and _pid_alive(int(rec.get("pid", 0))):
            return {"state": "running", "exit_code": None}
        if rec.get("cancelled"):
            return {"state": "cancelled", "exit_code": None}
        return {"state": "lost", "exit_code": None}

    def running_count(self) -> int:
        return sum(1 for r in self.all_records() if self.state(r)["state"] == "running")

    # -- start / cancel ------------------------------------------------------------------------
    def start(self, kind: str, argv: List[str], cwd: str, env: dict, meta: Optional[dict] = None) -> dict:
        self.job_dir.mkdir(parents=True, exist_ok=True)
        if self.running_count() >= MAX_CONCURRENT:
            raise JobLimitError("%d jobs are already running; wait for one to finish or cancel it with job_cancel"
                                % MAX_CONCURRENT)
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        log_path = self._path(job_id, ".log")
        log_path.write_text("$ %s\n" % " ".join(argv), encoding="utf-8")
        wrapper_argv = [sys.executable, str(WRAPPER), str(self.job_dir), job_id, cwd, "--"] + argv
        kwargs = dict(cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL)
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(wrapper_argv, **kwargs)
        self._procs[job_id] = proc
        rec = {"id": job_id, "kind": kind, "argv": argv, "cwd": cwd, "pid": proc.pid,
               "started_at": time.time(), "cancelled": False}
        rec.update(meta or {})
        self._write_record(rec)
        return rec

    def cancel(self, job_id: str) -> dict:
        rec = self.record(job_id)
        if rec is None:
            raise KeyError(job_id)
        st = self.state(rec)
        if st["state"] != "running":
            return st
        rec["cancelled"] = True
        self._write_record(rec)
        pid = int(rec["pid"])
        try:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, timeout=30)
            else:
                os.killpg(pid, signal.SIGTERM)
                for _ in range(50):
                    if not _pid_alive(pid) or (job_id in self._procs and self._procs[job_id].poll() is not None):
                        break
                    time.sleep(0.1)
                else:
                    os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        proc = self._procs.get(job_id)
        if proc is not None:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        return self.state(rec)

    def log_tail(self, job_id: str, lines: int, max_bytes: int) -> str:
        p = self._path(job_id, ".log")
        if not p.is_file():
            return ""
        size = p.stat().st_size
        with open(p, "rb") as fh:
            fh.seek(max(0, size - max_bytes * 4))
            data = fh.read().decode("utf-8", errors="replace")
        tail = "\n".join(data.splitlines()[-max(1, lines):])
        if len(tail.encode("utf-8")) > max_bytes:
            tail = tail.encode("utf-8")[-max_bytes:].decode("utf-8", errors="ignore")
        return tail

    def log_path(self, job_id: str) -> Path:
        return self._path(job_id, ".log")
