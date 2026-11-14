"""Runs one background job and records its exit code. Started detached by jobs.py.

Usage: python _job_wrapper.py <job_dir> <job_id> <cwd> -- <argv...>

The job keeps running if the MCP server (or its client) goes away; the exit code lands in
<job_dir>/<job_id>.exit, so job_status works after a reconnect. Standard library only.
"""

import os
import subprocess
import sys


def main() -> int:
    job_dir, job_id, cwd = sys.argv[1], sys.argv[2], sys.argv[3]
    if sys.argv[4] != "--":
        return 2
    argv = sys.argv[5:]
    log_path = os.path.join(job_dir, job_id + ".log")
    exit_path = os.path.join(job_dir, job_id + ".exit")
    code = 127
    with open(log_path, "ab", buffering=0) as log:
        try:
            p = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            code = p.wait()
        except OSError as e:
            log.write(("\nERROR: could not start %s: %s\n" % (argv[0] if argv else "?", e)).encode())
            code = 127
    tmp = exit_path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(str(code))
    os.replace(tmp, exit_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
