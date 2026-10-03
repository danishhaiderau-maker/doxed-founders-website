"""Run a long read-only command inside the Fly machine without one long exec.

``flyctl machine exec`` is a synchronous API call that Fly cuts off at roughly
60 s (HTTP 408), regardless of ``--timeout``.  A loaded machine (for example
the first-boot receipt bootstrap) can push the clean-epoch reset plan past
that limit, so the plan never runs.  This helper starts the command as a
detached in-machine job that writes its output and exit code to files, then
polls those files with short, retried execs and replays the job's output on
stdout with the job's exit code.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import time

JOB_DIR = "/tmp/fly-detached-exec"

_LAUNCHER = r'''
import json, os, subprocess, sys
job_dir, label, argv = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
os.makedirs(job_dir, exist_ok=True)
base = os.path.join(job_dir, label)
for suffix in (".out", ".rc", ".pid"):
    try:
        os.remove(base + suffix)
    except FileNotFoundError:
        pass
script = (
    "import subprocess, sys, os\n"
    "out = open(sys.argv[1] + '.out', 'wb')\n"
    "rc = subprocess.call(sys.argv[2:], stdout=out, stderr=subprocess.STDOUT)\n"
    "out.close()\n"
    "tmp = sys.argv[1] + '.rc.tmp'\n"
    "open(tmp, 'w').write(str(rc))\n"
    "os.replace(tmp, sys.argv[1] + '.rc')\n"
)
proc = subprocess.Popen(
    [sys.executable, "-c", script, base] + argv,
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    start_new_session=True,
)
open(base + ".pid", "w").write(str(proc.pid))
print(json.dumps({"schema": "fly_detached_exec_v1", "started": True, "label": label, "pid": proc.pid}))
'''

_POLLER = r'''
import json, os, sys
base = os.path.join(sys.argv[1], sys.argv[2])
if not os.path.exists(base + ".rc"):
    print(json.dumps({"schema": "fly_detached_exec_v1", "done": False}))
else:
    rc = int(open(base + ".rc").read().strip() or "1")
    out = open(base + ".out", "rb").read().decode("utf-8", "replace") if os.path.exists(base + ".out") else ""
    print(json.dumps({"schema": "fly_detached_exec_v1", "done": True, "rc": rc, "output": out}))
'''


def guest_command(program: str, *args: str) -> str:
    encoded = base64.b64encode(program.encode("utf-8")).decode("ascii")
    quoted = " ".join("'" + arg.replace("'", "'\"'\"'") + "'" for arg in args)
    return f"python -c 'import base64; exec(base64.b64decode(\"{encoded}\"))' {quoted}"


def parse_guest_json(stdout: str) -> dict:
    rows = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("schema") == "fly_detached_exec_v1":
                rows.append(row)
    if len(rows) != 1:
        raise RuntimeError("guest returned no single fly_detached_exec_v1 row")
    return rows[0]


def flyctl_exec(app: str, machine: str, command: str, *, timeout: int, run=subprocess.run) -> str:
    result = run(
        ["flyctl", "machine", "exec", "--app", app, "--timeout", str(timeout), machine, command],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()[-1:] or [""]
        raise RuntimeError(f"flyctl exec failed rc={result.returncode}: {detail[0][:300]}")
    return result.stdout


def retried(fn, *, attempts: int, sleep, label: str):
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except RuntimeError as exc:
            last = exc
            print(f"detached-exec {label} attempt={attempt} error={exc}", file=sys.stderr, flush=True)
            sleep(min(5 * attempt, 30))
    raise RuntimeError(f"detached-exec {label} failed after {attempts} attempts: {last}")


def run_detached(app: str, machine: str, label: str, argv: list[str], *, deadline_sec: int,
                 poll_sec: float = 10.0, run=subprocess.run, sleep=time.sleep,
                 monotonic=time.monotonic) -> tuple[int, str]:
    if not re.fullmatch(r"[a-z0-9-]{1,40}", label):
        raise ValueError("label must be [a-z0-9-]{1,40}")
    if not argv:
        raise ValueError("command is required")
    launch = guest_command(_LAUNCHER, JOB_DIR, label, json.dumps(argv))
    started = retried(
        lambda: parse_guest_json(flyctl_exec(app, machine, launch, timeout=45, run=run)),
        attempts=4, sleep=sleep, label=f"{label} launch",
    )
    if started.get("started") is not True:
        raise RuntimeError("detached job did not start")
    poll = guest_command(_POLLER, JOB_DIR, label)
    deadline = monotonic() + deadline_sec
    while monotonic() < deadline:
        try:
            row = parse_guest_json(flyctl_exec(app, machine, poll, timeout=45, run=run))
        except RuntimeError as exc:
            print(f"detached-exec {label} poll error={exc}", file=sys.stderr, flush=True)
            sleep(poll_sec)
            continue
        if row.get("done") is True:
            return int(row.get("rc", 1)), str(row.get("output") or "")
        sleep(poll_sec)
    raise RuntimeError(f"detached-exec {label} did not finish within {deadline_sec}s")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True)
    parser.add_argument("--machine", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--deadline-sec", type=int, default=1800)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    rc, output = run_detached(args.app, args.machine, args.label, command, deadline_sec=args.deadline_sec)
    sys.stdout.write(output)
    if output and not output.endswith("\n"):
        sys.stdout.write("\n")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
