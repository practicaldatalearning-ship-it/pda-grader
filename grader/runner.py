"""Sandboxed notebook execution via Docker + papermill.

Every submission runs with ALL of: --network none, non-root, read-only ROOTFS,
dropped caps, no-new-privileges, CPU/RAM/PID caps, a hard wall-clock timeout,
ephemeral (--rm). See STRICT §1.4.

Isolation vs. writability: the container's own filesystem is read-only; the ONLY
writable paths are a tmpfs `/tmp` and a single ephemeral bind mount at `/work`
(a throwaway copy of the student's notebook + data). Student code writes its
outputs (predictions.csv, the answer dump) into `/work`; we harvest them there
after the run. Nothing the student writes can touch the host or persist.
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("grader.runner")


@dataclass
class RunResult:
    ok: bool
    work_dir: Path
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None
    artifacts: dict[str, bytes] = field(default_factory=dict)


# Hardening flags applied to every student run.
def _sandbox_flags() -> list[str]:
    mem = os.environ.get("GRADER_MEM", "2g")
    return [
        "--rm",
        "--network", "none",
        "--memory", mem,
        "--memory-swap", mem,             # no swap beyond memory
        "--cpus", os.environ.get("GRADER_CPUS", "1"),
        "--pids-limit", "256",
        "--read-only",                    # rootfs read-only; only tmpfs + /work bind are writable
        "--tmpfs", "/tmp:size=512m,exec",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", "1000:1000",
        # Writable scratch/config dirs live under the tmpfs so the read-only rootfs
        # doesn't break papermill/jupyter/matplotlib.
        "-e", "HOME=/tmp",
        "-e", "JUPYTER_RUNTIME_DIR=/tmp/jupyter",
        "-e", "MPLCONFIGDIR=/tmp/mpl",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-e", "OPENBLAS_NUM_THREADS=1",
        "-e", "OMP_NUM_THREADS=1",
    ]


def run_notebook(
    image: str,
    work_dir: Path,
    out_dir: Optional[Path] = None,  # accepted for API symmetry; artifacts come from work_dir
    nb_name: str = "nb.ipynb",
    timeout: int = 300,
    collect: tuple[str, ...] = ("answers.json", "tests.json", "predictions.csv", "executed.ipynb"),
) -> RunResult:
    """Execute work_dir/nb_name inside the sandbox; harvest artifacts from work_dir."""
    work_dir = Path(work_dir).resolve()

    # `timeout` (coreutils) caps wall-clock even if papermill hangs; papermill's
    # own --execution-timeout caps a single stuck cell. mkdir the tmpfs scratch dirs.
    inner = (
        "mkdir -p /tmp/jupyter /tmp/mpl && "
        f"timeout {int(timeout)} papermill /work/{nb_name} /work/executed.ipynb "
        f"-k python3 --execution-timeout {int(timeout)} --no-progress-bar --cwd /work"
    )
    cmd = [
        "docker", "run",
        *_sandbox_flags(),
        "-v", f"{work_dir}:/work",   # writable ephemeral bind mount
        "-w", "/work",
        image,
        "bash", "-lc", inner,
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 60)
    except subprocess.TimeoutExpired as e:
        return RunResult(ok=False, work_dir=work_dir,
                         stdout=e.stdout if isinstance(e.stdout, str) else "",
                         stderr=e.stderr if isinstance(e.stderr, str) else "",
                         error=f"execution exceeded {timeout}s wall-clock")
    except FileNotFoundError:
        return RunResult(ok=False, work_dir=work_dir, error="docker not available on the runner")

    artifacts: dict[str, bytes] = {}
    for name in collect:
        p = work_dir / name
        if p.exists():
            try:
                artifacts[name] = p.read_bytes()
            except Exception:
                pass

    ok = proc.returncode == 0
    err = None
    if not ok:
        err = f"notebook run failed (exit {proc.returncode})"
        if proc.returncode == 124:
            err = f"execution exceeded {timeout}s wall-clock"
    return RunResult(ok=ok, work_dir=work_dir, stdout=(proc.stdout or "")[-8000:],
                     stderr=(proc.stderr or "")[-8000:], error=err, artifacts=artifacts)
