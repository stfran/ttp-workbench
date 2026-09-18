# adapters/base_adapter.py  
# Base class for all tool adapters
# predict function is the main entry point
from __future__ import annotations
from pathlib import Path
from typing  import Iterable, List, Dict, Any, Tuple
import uuid, json, os, tempfile
from tempfile import NamedTemporaryFile
from datetime import datetime
import numbers
from tqdm import tqdm
import subprocess
from contextlib import contextmanager

from Framework.adapters.docker_session import DockerSession
from Framework.utils.attack_lookup import get_ttp_name

PROJ_ROOT = Path(__file__).parent.parent.parent


def _fmt_float(val: float, places: int = 4) -> str:
    # Scale percentages >1 into [0,1], then format
    if val > 1.0:
        val = val / 100.0
    return f"{val:.{places}f}"


def _normalize_value(v: Any) -> Any:
    if isinstance(v, numbers.Real) and not isinstance(v, bool):
        return _fmt_float(float(v), 4)
    return v


def normalize_sentences(sentence_list: List[Dict]) -> List[Dict]:
    """
    Output structure per sentence:
    {
      'text': <sentence text>,
      'ttps': [
         {'code': 'T0123', 'TTP': 'Some TTP name', <other-keys-preserved-and-floats-normalized>},
         ...
      ]
    }
    """
    updated_sentence_list: List[Dict[str, Any]] = []

    for sentence_dict in sentence_list:
        ttps = sentence_dict.get('ttps') or []
        if not isinstance(ttps, list):
            raise ValueError("[ERROR] 'ttps' must be a list of dictionaries")

        updated_ttps: List[Dict[str, Any]] = []

        for ttp in ttps:
            if not isinstance(ttp, dict):
                raise ValueError("[ERROR] Each item in 'ttps' must be a dict")

            # Find code
            if 'code' in ttp.keys():
                ttp_code = ttp['code']
            elif 'label' in ttp.keys():
                ttp_code = ttp['label']
            else:
                raise ValueError("[ERROR] Unexpected TTP entry; expected 'code' or 'label'")

            # Preserve all other keys except label/code
            other_items = {
                k: _normalize_value(v)
                for k, v in ttp.items()
                if k not in ('code', 'label')
            }  
            try:
                ttp_name = get_ttp_name(ttp_code)
            except Exception:
                ttp_name = None
            updated_ttp = {
                'code': ttp_code,
                'TTP': ttp_name if ttp_name else "Unknown TTP",
            }
            updated_ttp.update(other_items)

            updated_ttps.append(updated_ttp)

        updated_sentence_list.append({
            'text': sentence_dict.get('text', "").strip(),
            'ttps': updated_ttps
        })

    return updated_sentence_list


def _safe_name(s: str | None, default: str) -> str:
    if not s:
        return default
    keep = [c if c.isalnum() or c in ("-", "_", ".") else "_" for c in s]
    return "".join(keep).strip("._") or default


class BaseAdapter:
    """
    Subclasses customise:
        - build_command()
        - parse_sentences()
        - parse_ttps()
    Pass through to docker session
    """
    image: str  # container image to run
    workdir: str | None = None
    env: dict | None = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs

        # Subclasses may set self.env before super().__init__(), or may update
        # self.env later. kwargs["env"] lets runners inject additional env vars.
        adapter_env = getattr(self, "env", None) or {}
        kw_env = kwargs.get("env") or {}

        if not isinstance(adapter_env, dict):
            raise TypeError(f"{self.__class__.__name__}.env must be a dict or None")
        if not isinstance(kw_env, dict):
            raise TypeError("BaseAdapter env kwarg must be a dict or None")

        merged_env = {}
        merged_env.update(adapter_env)
        merged_env.update(kw_env)

        # Docker env values should be strings. Drop None values so callers can
        # omit optional secrets without exporting literal 'None'.
        self.env = {
            str(k): str(v)
            for k, v in merged_env.items()
            if v is not None
        } or None

        # Determine host-side temp root from a single kwarg: tmp_root
        tmp_root = kwargs.get("tmp_root", PROJ_ROOT / "tmp")
        if tmp_root:
            try:
                candidate = Path(tmp_root).expanduser().resolve()
                candidate.mkdir(parents=True, exist_ok=True)
                # writability probe
                probe = candidate / f".probe_{uuid.uuid4().hex}"
                with probe.open("w", encoding="utf-8") as fh:
                    fh.write("ok")
                probe.unlink(missing_ok=True)
                self._tmp_root = candidate
            except Exception as e:
                print(
                    f"[WARNING] tmp_root='{tmp_root}' not usable ({type(e).__name__}: {e}); "
                    f"falling back to system temp: {tempfile.gettempdir()}"
                )
                self._tmp_root = Path(tempfile.gettempdir())
        else:
            self._tmp_root = Path(tempfile.gettempdir())

    # Expose for tests/inspection
    @property
    def tmp_root(self) -> Path:
        return self._tmp_root

    @contextmanager
    def _tool_output_section(self, label):
        # helper for verbose output so we can distinguish raw tool outputs from adapter outputs
        verbose = bool(self.kwargs.get("verbose", False))
        if verbose:
            print(
                f"\n=== RAW TOOL OUTPUT | {self.image} / framework / {label} ===",
                flush=True,
            )
        try:
            yield
        finally:
            if verbose:
                print(
                    "\n=== FRAMEWORK ADAPTER OUTPUT | postprocessing / cleanup ===",
                    flush=True,
                )
    

    def _print_tail(self, result: Any, max_lines: int = 30) -> None:
        # limit the stdout from noisy tools when we're in verbose mode
        if result is None or max_lines <= 0:
            return

        text_parts: list[str] = []

        # docker_session.exec() returns subprocess.CompletedProcess on success
        if isinstance(result, subprocess.CompletedProcess):
            if result.stdout:
                text_parts.append(str(result.stdout))
            if result.stderr:
                text_parts.append("[stderr]")
                text_parts.append(str(result.stderr))
        elif isinstance(result, str):
            text_parts.append(result)
        elif isinstance(result, dict):
            if result.get("stdout"):
                text_parts.append(str(result["stdout"]))
            if result.get("stderr"):
                text_parts.append("[stderr]")
                text_parts.append(str(result["stderr"]))

        if not text_parts:
            return

        combined = "\n".join(text_parts)
        lines = combined.splitlines()
        if not lines:
            return

        tail = lines[-max_lines:]
        tqdm.write("\n".join(tail))


    def _tmp_file_with(self, text: str) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        prefix = f"ttp_extraction_study_tmp_{timestamp}_"
        with NamedTemporaryFile(
            "w", delete=False, encoding="utf-8",
            prefix=prefix, suffix=".txt", dir=str(self._tmp_root)
        ) as tf:
            tf.write(text)
            return Path(tf.name)

    def _tmp_path(self, suffix: str = ".json") -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        prefix = f"ttp_extraction_study_tmp_{timestamp}_"
        fd, path = tempfile.mkstemp(
            prefix=prefix, suffix=suffix, dir=str(self._tmp_root)
        )
        os.close(fd)
        return Path(path)

    def _mkdtemp(self, *, prefix: str) -> Path:
        # Local helper so callers don't touch tempfile directly
        return Path(tempfile.mkdtemp(prefix=prefix, dir=str(self._tmp_root)))

    def _make_crash_log_path(
        self,
        save_dir: Path | None,
        prefix: str,
        rec_id: str | None = None,
        batch_uid: str | None = None,
    ) -> Path:
        
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        if rec_id:
            safe = _safe_name(str(rec_id), "noid")
            name = f"{prefix}__{safe}__crash_{ts}.log"
        else:
            safe = _safe_name(batch_uid or "batch", "batch")
            name = f"{prefix}__{ts}__{safe}__crash.log"
        root = Path(save_dir) if save_dir else self._tmp_root
        return root / name

    # -------------------------------------------------------------------
    # abstract hooks to be overridden by tool-specific adapter subclasses
    # -------------------------------------------------------------------
    def build_command(self, in_cn: str, out_cn: str) -> List[str]:
        raise NotImplementedError

    def build_bulk_command(self, in_dir_cn: str, out_dir_cn: str, manifest_cn: str | None) -> List[str] | None:
        raise NotImplementedError

    def parse_sentences(self, d: Dict[str, Any]) -> Dict[str, Any]:
        return []  # default: no post-processing

    def extract_ttps(self, d: Dict[str, Any]) -> List[Dict[str, Any]]:
        return []

    def prepare_session(self, ds: DockerSession) -> None:
        """
        Optional setup inside this disposable container, before prediction.
        We use this to copy specific versions of tools into the container when the standard image doesn't have the right version, or to pre-load models, etc.
        """
        pass

    def on_error_cleanup(self, ds: DockerSession) -> None:
        return

    # -------------------------------------------------------------------
    # predict instantiates a DockerSession and manages input and output with the session
    # predict is the pass through of the docker run command built with build_command()
    # -------------------------------------------------------------------
    # NOTE TO DO: refactor to splitup bulk predict 
    def predict(
        self,
        texts: Iterable[str],
        *,
        ids: Iterable[str] | None = None,
        save_dir: str | Path | None = None,
        prefix: str = "pred",
        bulk: bool = False,
    ) -> Tuple[List[Dict[str, Any]], List[Path | None]]:

        save_dir = Path(save_dir).expanduser() if save_dir else None
        if save_dir:
            save_dir.mkdir(parents=True, exist_ok=True)

        preds: List[Dict[str, Any]] = []
        files: List[Path | None] = []
        mem = self.kwargs.get("memory")
        gpus = self.kwargs.get("gpus")
        ipc = self.kwargs.get("ipc")
        shm_size = self.kwargs.get("shm_size")
        if gpus is None and self.kwargs.get("use_gpus"):
            gpus = True
        inactivity = self.kwargs.get("inactivity_timeout_s", None)
        soft_fail = self.kwargs.get("soft_fail", True)
        slightly_verbose = self.kwargs.get("slightly_verbose", False)
        verbose = self.kwargs.get("verbose", False)
        engine = self.kwargs.get("engine") or os.environ.get("CONTAINER_ENGINE", "docker")
        binds = self.kwargs.get("binds", [])
        selinux_label = self.kwargs.get("selinux_label", None)  # session manager init handles translating engine -> selinux label
        if verbose:
            slightly_verbose = True

        with DockerSession(
            self.image,
            memory=mem,
            gpus=gpus,
            require_gpus=self.kwargs.get("require_gpus", False),
            ipc=ipc,
            shm_size=shm_size,
            engine=engine,
            binds=binds,
            selinux_label=selinux_label,
        ) as ds:
            self.prepare_session(ds)
            id_iter = ids if ids is not None else (None for _ in texts)
            total = len(texts)
            if verbose:
                print(f"[INFO] Starting prediction for {total} items on {self.image.split(':')[-1]}")
                print(f"[INFO] Container engine: {engine}")
                print(f"[INFO] GPU support: {'enabled' if gpus else 'disabled'}")
                print(f"[INFO] Memory limit: {mem if mem else 'default'}")
                print(f"[INFO] IPC mode: {ipc if ipc else 'default'}")
                print(f"[INFO] SHM size: {shm_size if shm_size else 'default'}")
                print(f"[INFO] Inactivity timeout: {inactivity if inactivity else 'default'} seconds")
                print(f"[INFO] Soft-fail mode: {'enabled' if soft_fail else 'disabled'}")
                print(f"[INFO] Bulk processing mode: {'enabled' if bulk else 'disabled'}")
                print(f"[INFO] Host tmp_root: {self._tmp_root}")

            bulk_cmd = None
            if bulk:
                if verbose:
                    print(
                        f"[INFO] Preparing bulk processing command with [{total} items] on {self.image.split(':')[-1]}"
                    )
                batch_uid = uuid.uuid4().hex[:8]
                in_dir_cn = f"/tmp/batch_{batch_uid}/in"
                out_dir_cn = f"/tmp/batch_{batch_uid}/out"
                manifest_cn = f"/tmp/batch_{batch_uid}/manifest.json"

                # 0) prepare batch inputs
                host_in_root = self._mkdtemp(prefix="ttp_batch_in_")
                host_out_root = self._mkdtemp(prefix="ttp_batch_out_")
                try:
                    host_manifest = self._tmp_path(suffix=".json")
                    manifest: List[Dict[str, Any]] = []
                    texts_list = list(texts)
                    ids_list = list(ids) if ids is not None else [None] * len(texts_list)

                    for i, (txt, rec_id) in enumerate(zip(texts_list, ids_list), 1):
                        base = _safe_name(str(rec_id), f"item_{i:06d}")
                        fname = f"{i:06d}__{base}.txt"
                        fpath = host_in_root / fname
                        fpath.write_text(txt, encoding="utf-8")
                        manifest.append({"id": rec_id, "in": fname, "out": f"{i:06d}__{base}.json"})
                    host_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

                    # 1) copy in   ───────────────────────────────────────────
                    ds.exec("mkdir", "-p", in_dir_cn, out_dir_cn)
                    ds.cp_dir_to(host_in_root, in_dir_cn)
                    ds.cp_to(host_manifest, manifest_cn)

                    # 2) run tool  ───────────────────────────────────────────
                    # make sure we have a bulk command from downstream adapter
                    bulk_cmd = self.build_bulk_command(in_dir_cn, out_dir_cn, manifest_cn)

                    if bulk_cmd is None:
                        raise ValueError(
                            "[ERROR] bulk processing requested but build_bulk_command() returned None"
                        )
                    elif bulk_cmd:
                        if slightly_verbose:
                            print(
                                f"[INFO] Running bulk processing command with [{len(texts)} items] on {self.image.split(':')[-1]}"
                            )

                        err_msg = None
                        crash_log = self._make_crash_log_path(
                            save_dir, prefix, batch_uid=batch_uid
                        )
                        try:
                            monitor = self.kwargs.get("bulk_monitor")
                            with self._tool_output_section(f"batch {batch_uid}"):
                                if callable(monitor):
                                    monitor(
                                        self,
                                        ds,
                                        bulk_cmd,
                                        len(texts),
                                        inactivity,
                                        crash_log,
                                        soft_fail,
                                        bool(verbose),
                                    )
                                else:
                                    exec_result = ds.exec(
                                        *bulk_cmd,
                                        workdir=self.workdir,
                                        env=self.env,
                                        capture=not bool(verbose),
                                        inactivity_timeout_s=inactivity,
                                        crash_log=crash_log,
                                        soft_fail=soft_fail,
                                    )
                                    # Surface provider retry diagnostics while
                                    # leaving prompts and responses captured.
                                    if isinstance(exec_result, subprocess.CompletedProcess):
                                        for output in (exec_result.stdout, exec_result.stderr):
                                            for line in (output or "").splitlines():
                                                if line.startswith("TTPWB_OPENAI_RETRY "):
                                                    print(line, flush=True)
                        except Exception as e:
                            err_msg = f"[ERROR] bulk docker_session exec, {type(e).__name__}: {e}"

                        if err_msg:
                            for rec_id in ids_list:
                                preds.append(
                                    {
                                        "id": rec_id,  # if this exec failed, then for now, just error all of them as failed
                                        "ttps": None,
                                        "error": err_msg,
                                        "crash_log": str(crash_log),
                                        "sentences": None,
                                    }
                                )
                                files.append(None)
                            try:
                                self.on_error_cleanup(ds)
                            except Exception as e:
                                if slightly_verbose:
                                    print(
                                        f"[WARNING] on_error_cleanup() raised exception: {type(e).__name__}: {e}"
                                    )
                        else:
                            # 3) copy out  ───────────────────────────────────────────
                            ds.cp_dir_from(out_dir_cn, host_out_root)
                            # post process
                            id_by_outcome = {m["out"]: m["id"] for m in manifest}
                            for out_file in sorted(host_out_root.glob("*.json")):
                                rec_id = id_by_outcome.get(out_file.name)
                                try:
                                    with out_file.open("r", encoding="utf-8") as fh:
                                        raw = json.load(fh)

                                    # Persist raw outputs under save_dir so downstream runners can consume them
                                    dest_path = None
                                    if save_dir:
                                        # Use the clean record id as filename so collectors can map easily
                                        fname = f"{rec_id}.json" if rec_id is not None else out_file.name
                                        dest_path = Path(save_dir) / fname
                                        dest_path.write_text(
                                            json.dumps(raw, ensure_ascii=False, indent=2),
                                            encoding="utf-8",
                                        )

                                    preds.append(
                                        {
                                            "id": rec_id,
                                            "ttps": self.extract_ttps(raw),
                                            "sentences": normalize_sentences(self.parse_sentences(raw)),
                                        }
                                    )
                                    files.append(dest_path if save_dir else None)
                                except Exception as e:
                                    preds.append(
                                        {
                                            "id": rec_id,
                                            "ttps": None,
                                            "error": f"Output handling error: {type(e).__name__}: {e}",
                                            "sentences": None,
                                        }
                                    )
                                    files.append(None)

                            # apply errors to any that are missing
                            processed_ids = {p["id"] for p in preds if p.get("id") is not None}
                            for rec_id in ids_list:
                                if rec_id not in processed_ids:
                                    preds.append(
                                        {
                                            "id": rec_id,
                                            "ttps": None,
                                            "error": "No output produced for this record in bulk processing",
                                            "sentences": None,
                                        }
                                    )
                                    files.append(None)

                    # 4) clean-up  ───────────────────────────────────────────
                    ds.exec("rm", "-rf", f"/tmp/batch_{batch_uid}", capture=True, soft_fail=True)
                finally:
                    # clean up host temp
                    for f in host_in_root.iterdir():
                        f.unlink(missing_ok=True)
                    host_in_root.rmdir()
                    for f in host_out_root.iterdir():
                        f.unlink(missing_ok=True)
                    # Always remove the temp directory; raw copies (if any) were persisted to save_dir
                    host_out_root.rmdir()

            if not bulk_cmd:

                for idx, (txt, rec_id) in enumerate(
                    tqdm(zip(texts, id_iter), total=total, desc=f"Processing texts"), 1
                ):
                    if slightly_verbose:
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        print(
                            f"[INFO] Running text #{idx}/{total} on {self.image.split(':')[-1]} at {timestamp}"
                        )
                    uid = uuid.uuid4().hex
                    in_cn = f"/tmp/in_{uid}.txt"
                    out_cn = f"/tmp/out_{uid}.json"
                    host_in = self._tmp_file_with(txt)
                    host_out = (
                        (save_dir / f"{prefix}_raw_{rec_id}.json") if save_dir else self._tmp_path()
                    )

                    # 1) copy in   ───────────────────────────────────────────
                    ds.cp_to(host_in, in_cn)

                    # 2) run tool  ───────────────────────────────────────────
                    err_msg = None
                    crash_log = self._make_crash_log_path(save_dir, prefix, rec_id=rec_id)
                    try:
                        command = self.build_command(in_cn, out_cn)
                        with self._tool_output_section(rec_id):
                            result = ds.exec(
                                *command,
                                workdir=self.workdir,
                                env=self.env,
                                capture=not bool(verbose),
                                inactivity_timeout_s=inactivity,
                                crash_log=crash_log,
                                soft_fail=soft_fail,
                            )
                        if result is None:
                            err_msg = f"Tool failed ({inactivity}s timeout or non-zero exit; soft-failure)"
                    except Exception as e:
                        err_msg = f"[ERROR] call to docker_session exec, {type(e).__name__}: {e}"

                    if err_msg:
                        preds.append(
                            {
                                "id": rec_id,
                                "ttps": None,
                                "error": err_msg,
                                "crash_log": str(crash_log),
                                "sentences": None,
                            }
                        )
                        files.append(None)
                        # Best-effort cleanup inside container
                        try:
                            ds.exec("rm", "-f", in_cn, out_cn, capture=True, soft_fail=True)
                        except Exception:
                            pass
                        # Cleanup host temp
                        host_in.unlink(missing_ok=True)
                        if not save_dir:
                            host_out.unlink(missing_ok=True)
                        continue

                    # 3) copy out  ───────────────────────────────────────────
                    try:
                        ds.cp_from(out_cn, host_out)
                        with host_out.open("r", encoding="utf-8") as fh:
                            raw = json.load(fh)
                        preds.append(
                            {
                                "id": rec_id,
                                "ttps": self.extract_ttps(raw),
                                "sentences": normalize_sentences(self.parse_sentences(raw)),
                            }
                        )
                        files.append(host_out if save_dir else None)
                    except Exception as e:
                        preds.append(
                            {
                                "id": rec_id,
                                "ttps": None,
                                "error": f"Output handling error: {type(e).__name__}: {e}",
                                "sentences": None,
                            }
                        )
                        files.append(None)

                    # 4) clean-up  ───────────────────────────────────────────
                    ds.exec("rm", "-f", in_cn, out_cn)
                    host_in.unlink(missing_ok=True)
                    if not save_dir:
                        host_out.unlink(missing_ok=True)

        return preds, files
