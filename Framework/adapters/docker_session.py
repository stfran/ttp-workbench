# adapters/docker_session.py
# manages the lifecycle of container interactions
from __future__ import annotations
import subprocess, json
from pathlib import Path
from typing import List, Optional
import os, tempfile, threading, queue, time, re

n = os.cpu_count() or 1
t = str(max(1, n - 1))
env = os.environ.copy()
env.update({ 
  "PYTHONUNBUFFERED": "1",
  # Math stacks
  "OMP_NUM_THREADS": t,
  "OPENBLAS_NUM_THREADS": t,
  "MKL_NUM_THREADS": t,
  "BLIS_NUM_THREADS": t,           
  "NUMEXPR_NUM_THREADS": t,
  # Politeness
  "OMP_WAIT_POLICY": "PASSIVE",
  "KMP_BLOCKTIME": "1",
  # Misc
  "TOKENIZERS_PARALLELISM": "false",
})

VALID_ENGINES = {"docker", "podman"}

def _run(cmd: List[str], **kw):
    kw.setdefault("check", True)
    kw.setdefault("text", True)
    try:
        return subprocess.run(cmd, **kw)
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] command failed: {' '.join(map(str, e.cmd))}", flush=True)
        if getattr(e, "stdout", None):
            print(f"[ERROR] stdout:\n{e.stdout}", flush=True)
        if getattr(e, "stderr", None):
            print(f"[ERROR] stderr:\n{e.stderr}", flush=True)
        raise

def _gpus_supported(image: str, engine: str, spec: str = "all") -> bool:
    """
    Try to start a no-op container with GPU access.

    Docker uses: --gpus <spec>
    Podman uses: --device nvidia.com/gpu=<spec>
    """
    if engine == "docker":
        probe = ["docker", "run", "--rm", "--gpus", spec, "--entrypoint", "sh", image, "-lc", ":"]
    elif engine == "podman":
        probe = ["podman", "run", "--rm", "--device", f"nvidia.com/gpu={spec}", "--entrypoint", "sh", image, "-lc", ":"]
    else:
        raise ValueError(f"Invalid engine {engine!r}, must be one of {VALID_ENGINES}")

    # A stale runtime lock can make an otherwise valid Podman probe fail once.
    # Bound every attempt so an image entrypoint or runtime fault cannot hang
    # adapter startup indefinitely.
    for _ in range(2):
        try:
            p = subprocess.run(probe, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            continue
        if p.returncode == 0:
            return True
    return False


def _gpu_args(engine: str, spec: str = "all") -> List[str]:
    """
    Return standard GPU runtime arguments for the selected container engine.
    """
    if engine == "docker":
        return ["--gpus", spec]
    if engine == "podman":
        return ["--device", f"nvidia.com/gpu={spec}"]
    raise ValueError(f"Invalid engine {engine!r}, must be one of {VALID_ENGINES}")

class DockerSession:
    """Context-manager that keeps one container alive."""
    def __init__(self, image: str, *, memory: str | None = None,
                 gpus: str | bool | None = None,
                 require_gpus: bool = False,
                 ipc: str | None = None,                 # e.g., "host"
                 shm_size: str | None = None,           # e.g., "1g"
                 engine: str = "docker",
                 binds: list[tuple[str, str]] | None = None,   # [(host_path, container_path), ...], this is lighter than committing when we want to save training results
                 selinux_label: str | None = None              # ":Z" (podman) | None
                 ):      
        if engine not in VALID_ENGINES:
            raise ValueError(f"Invalid engine {engine!r}, must be one of {VALID_ENGINES}")
        self._bin = engine
        self.image    = image
        self.memory   = memory
        self.gpus     = gpus
        self.require_gpus = require_gpus
        self.ipc      = ipc
        self.shm_size = shm_size
        self.binds    = list(binds or [])
        if engine == "podman" and selinux_label is None:
            selinux_label = ":Z"
        self.selinux_label = selinux_label
        self.cid: str | None = None

    # ──────────────────────────────────────────────────────────────
    def __enter__(self):
        cmd = [self._bin, "run", "-d", "--rm"]
        if self.memory:
            mem_swap = f"{int(self.memory[:-1]) + 4}g"
            cmd += ["--memory", self.memory, "--memory-swap", mem_swap]
        if self.gpus:
            spec = "all" if self.gpus is True else str(self.gpus)
            if _gpus_supported(self.image, self._bin, spec):
                cmd += _gpu_args(self._bin, spec)
            else:
                if self.require_gpus:
                    raise RuntimeError(
                        f"GPU requested but unavailable for {self._bin}; refusing CPU fallback."
                    )
                print(
                    f"[WARN] GPU requested but unavailable for {self._bin}; running without GPU.",
                    flush=True,
                )
                
        if self.ipc:
            cmd += ["--ipc", self.ipc]
        if self.shm_size:
            cmd += ["--shm-size", self.shm_size]

        for (host_p, cn_p) in self.binds:
            host_p = str(Path(host_p).expanduser().absolute())
            Path(host_p).mkdir(parents=True, exist_ok=True)
            if self._bin == "docker":
                cmd += ["-v", f"{host_p}:{cn_p}"]
            else:  # podman needs SELinux label when enforcing; default to :Z
                label = self.selinux_label if self.selinux_label is not None else ":Z"
                cmd += ["-v", f"{host_p}:{cn_p}{label}"]

        cmd += ["--entrypoint", "sleep", self.image, "infinity"]
        self.cid = _run(cmd, capture_output=True).stdout.strip()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.cid:
            subprocess.run([self._bin, "stop", self.cid],
                            stdout=subprocess.DEVNULL, check=False)
            still_running = subprocess.run(
                [self._bin, "inspect", "-f", "{{.State.Running}}", self.cid],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
            )
            
            if still_running.stdout.strip() == "true":
                subprocess.run(
                    [self._bin, "kill", "-s", "KILL", self.cid],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )

            # remove
            subprocess.run(
                [self._bin, "rm", "-f", self.cid],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            print(f"[INFO] DockerSession: container {self.cid} stopped and removed. exc_type={exc_type}, exc={exc}, tb={tb}", flush=True)

    # ── cp helpers ────────────────────────────────────────────────
    def cp_to(self, src: Path, dst: str):
        _run([self._bin, "cp", str(src), f"{self.cid}:{dst}"])

    def cp_from(self, src: str, dst: Path):
        _run([self._bin, "cp", f"{self.cid}:{src}", str(dst)])

    def cp_dir_to(self, src_dir: Path, dst_dir_cn: str):
        """Copy the *contents* of a host directory into the container directory."""
        src_dir = Path(src_dir)
        # IMPORTANT: the trailing '/.' means "copy contents", not the directory itself
        _run([self._bin, "cp", str(src_dir) + "/.", f"{self.cid}:{dst_dir_cn}/"])

    def cp_dir_from(self, src_dir_cn: str, dst_dir: Path):
        """Copy a container directory to a host directory."""
        dst_dir = Path(dst_dir)
        dst_dir.mkdir(parents=True, exist_ok=True)
        _run([self._bin, "cp", f"{self.cid}:{src_dir_cn}/.", str(dst_dir)])

    def _wrap_line_buffering(self, cmd: str) -> str:
        # Ensure timely log flushing from Python and most CLIs
        # This supports the stdout/stderr line-reading threads so we can do verbose output
        return f"PYTHONUNBUFFERED=1 stdbuf -oL -eL {cmd}"

    def _default_crash_log_path(self) -> Path:
        return Path(tempfile.gettempdir()) / f"ttp_extraction_crash_{(self.cid or 'nocid')[:8]}.log"

    def _build_exec_cmd(
        self,
        args: List[str],
        workdir: str | None,
        env: dict | None
    ) -> List[str]:
        import shlex
        cmd: List[str] = [self._bin, "exec", "-i"]
        if workdir:
            cmd += ["-w", workdir]

        # env + unbuffered/line-buffered IO
        # env = dict(env or {})
        # env.setdefault("PYTHONUNBUFFERED", "1")
        # for k, v in env.items():
        #     cmd += ["-e", f"{k}={v}"]

        # Pass only variable names in argv. Docker and Podman resolve ``-e NAME``
        # from their own process environment, which keeps credential values out
        # of ``ps`` output and crash-log command lines.
        for name in self._exec_env_values(env):
            cmd += ["-e", name]

        inner = " ".join(shlex.quote(a) for a in args) if args else "true" 
        inner = self._wrap_line_buffering(inner)

        cmd += [self.cid, "/bin/bash", "-lc", inner]
        return cmd

    @staticmethod
    def _exec_env_values(values: dict | None) -> dict[str, str]:
        normalized = {str(key): str(value) for key, value in dict(values or {}).items()}
        normalized.setdefault("PYTHONUNBUFFERED", "1")
        invalid = [name for name in normalized if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)]
        if invalid:
            raise ValueError(f"Invalid container environment variable name: {invalid[0]!r}")
        return normalized

    def _exec_process_env(self, values: dict | None) -> dict[str, str]:
        process_env = os.environ.copy()
        process_env.update(self._exec_env_values(values))
        return process_env

    def _start_process(
        self,
        cmd: List[str],
        stdin: str | None,
        process_env: dict[str, str] | None = None,
    ) -> subprocess.Popen:
        p = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if stdin is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line buffered
            env=process_env,
        )
        if stdin is not None:
            try:
                p.stdin.write(stdin)
                p.stdin.flush()
            finally:
                try:
                    p.stdin.close()
                except Exception:
                    pass
        return p

    def _start_stream_threads(
        self,
        p: subprocess.Popen,
        tee: bool
    ):
        import sys

        q_activity: "queue.Queue[float]" = queue.Queue()
        stdout_buf: list[str] = []
        stderr_buf: list[str] = []
        state = {"last_out": "", "last_err": ""}

        def _reader(stream, sink, is_err: bool):
            for line in iter(stream.readline, ""):
                sink.append(line)
                if tee:
                    print(line, end="", file=(sys.stderr if is_err else sys.stdout), flush=True)
                if is_err:
                    state["last_err"] = line.rstrip("\n")
                else:
                    state["last_out"] = line.rstrip("\n")
                try:
                    q_activity.put_nowait(time.time())
                except queue.Full:
                    pass
            try:
                stream.close()
            except Exception:
                pass

        t_out = threading.Thread(target=_reader, args=(p.stdout, stdout_buf, False), daemon=True)
        t_err = threading.Thread(target=_reader, args=(p.stderr, stderr_buf, True), daemon=True)
        t_out.start()
        t_err.start()
        return q_activity, stdout_buf, stderr_buf, state, t_out, t_err

    def _monitor_process(
        self,
        p: subprocess.Popen,
        q_activity: "queue.Queue[float]",
        inactivity_timeout_s: Optional[int]
    ) -> bool:
        import math
        timed_out = False
        last_activity = time.monotonic()
        armed = inactivity_timeout_s is not None and inactivity_timeout_s > 0
        #if armed:
        #    print(f"[watchdog] armed: {inactivity_timeout_s}s of silence -> timeout", flush=True)

        while True:
            try:
                ts = q_activity.get(timeout=0.25)
                last_activity = time.monotonic()  # reset on any line
            except queue.Empty:
                pass

            ret = p.poll()
            if ret is not None:
                break

            if armed:
                idle = time.monotonic() - last_activity
                # print once at ~50% to prove it’s ticking
                # if math.isclose(idle, inactivity_timeout_s/2, rel_tol=0.0, abs_tol=0.3):
                #    print(f"[watchdog] half-time idle={idle:.1f}s", flush=True)
                if idle > inactivity_timeout_s:
                    timed_out = True
                    try:
                        p.terminate()
                        p.wait(timeout=3)
                    except Exception:
                        try: p.kill()
                        except Exception: pass
                    break
        return timed_out


    def _collect_output(
        self,
        p: subprocess.Popen,
        t_out: threading.Thread,
        t_err: threading.Thread,
        stdout_buf: List[str],
        stderr_buf: List[str]
    ) -> tuple[str, str]:
        t_out.join(timeout=1.0)
        t_err.join(timeout=1.0)
        stdout = "".join(stdout_buf)
        stderr = "".join(stderr_buf)
        return stdout, stderr

    def _dump_crash_log(
        self,
        cmd: List[str],
        reason: str,
        stdout: str,
        stderr: str,
        last_line_out: str,
        last_line_err: str,
        crash_log: Optional[Path]
    ) -> Path:
        try:
            state_json = subprocess.check_output([self._bin, "inspect", self.cid], text=True)
            state = json.loads(state_json)[0].get("State", {})
        except Exception as inner:
            state = {"inspect_error": str(inner)}

        path = crash_log or self._default_crash_log_path()
        path.write_text(
            "===== CMD =====\n" + " ".join(cmd) + "\n\n"
            f"===== ERROR =====\n{reason}\n"
            f"last_stdout_line={last_line_out!r}\nlast_stderr_line={last_line_err!r}\n\n"
            f"===== STATE =====\n{json.dumps(state, indent=2)}\n\n"
            f"===== STDOUT (partial) =====\n{stdout}\n\n"
            f"===== STDERR (partial) =====\n{stderr}\n",
            encoding="utf-8"
        )
        print(f"[docker_session] ⚠  {reason}; dumped log to {path}")
        return path

    # ---------- exec (orchestrator) ----------
    def exec(self, *args: str,
             stdin: str | None = None,
             capture: bool = False,
             crash_log: Optional[Path] = None,
             workdir: str | None = None,
             env: dict | None = None,
             inactivity_timeout_s: Optional[int] = None,
             soft_fail: bool = False):

        if capture and crash_log is None:
            crash_log = self._default_crash_log_path()

        cmd = self._build_exec_cmd(list(args), workdir, env)
        """# DEBUG the inactivity_timer_s
        if inactivity_timeout_s is None or inactivity_timeout_s <= 0:
            print("[watchdog] not armed (no inactivity_timeout_s provided)", flush=True)
        else:
            print(f"[exec] inactivity_timeout_s={inactivity_timeout_s}", flush=True)
        """
        p = self._start_process(cmd, stdin, self._exec_process_env(env))

        tee = not capture
        q_activity, stdout_buf, stderr_buf, state, t_out, t_err = self._start_stream_threads(p, tee)

        timed_out = self._monitor_process(p, q_activity, inactivity_timeout_s)
        stdout, stderr = self._collect_output(p, t_out, t_err, stdout_buf, stderr_buf)

        if timed_out:
            self._dump_crash_log(
                cmd,
                f"INACTIVITY TIMEOUT after {inactivity_timeout_s}s",
                stdout, stderr,
                state["last_out"], state["last_err"],
                crash_log
            )
            if soft_fail:
                return None
            raise subprocess.CalledProcessError(124, cmd, output=stdout, stderr=stderr)

        if p.returncode != 0:
            if soft_fail:
                self._dump_crash_log(
                    cmd,
                    f"NON-ZERO EXIT {p.returncode}",
                    stdout, stderr,
                    state["last_out"], state["last_err"],
                    crash_log
                )
                return None
            raise subprocess.CalledProcessError(p.returncode, cmd, output=stdout, stderr=stderr)

        return subprocess.CompletedProcess(cmd, returncode=p.returncode, stdout=stdout, stderr=stderr)
