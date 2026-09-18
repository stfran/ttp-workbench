from __future__ import annotations
from typing import List, Dict, Any, Tuple, Iterable
from pathlib import Path
import os

from Framework.adapters.base_adapter import BaseAdapter
from Framework.adapters.docker_session import _run, _gpus_supported, VALID_ENGINES, _gpu_args
from Framework.utils.attack_lookup import is_valid_ttp_code

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

class BuchelAdapter(BaseAdapter):
    """
    Adapter for the Buchel CLI which reuses the two containers
    """

    # Container/image settings
    image   = "localhost/generation_app:latest" # created by Buchel setup script
    workdir = "/workspace"                # project root inside the image 
    tool_path = "buchel_cli.py"

    # Execution settings
    _python_bin = "python3"  
    
    # Ollama settings
    _ollama_image = "localhost/generation_ollama:latest"
    _ollama_name  = "ollama_local"
    _ollama_port  = os.environ.get("OLLAMA_PORT", "11434")

    def __init__(self, *, verbose: bool = False,
                # Inference strategies
                base_model: str = "unsloth/Meta-Llama-3.1-8B-Instruct",
                document_level: bool = False,
                rag: bool = False,
                fsp: bool = False,
                quant_4bit_model: bool = False,
                # SFT options
                sft: bool = False,
                sft_name: str | None = None,
                sft_model_dir: str | None = None,
                auto_train: bool = False,
                prefer_merged: bool = True,
                dataset_path: str | None = None,
                host_output_dir: str | os.PathLike | None = None,
                host_hf_cache: str | os.PathLike | None = None,
                dataset: str | None = None,
                **kwargs):
        self.verbose = verbose
        self.flags = []

        # Model selection flags
        if base_model:
            self.flags += ["--base_model", base_model]
        if document_level:
            self.flags += ["--document_level"]
        if rag:
            self.flags += ["--rag"]
        if fsp:
            self.flags += ["--fsp"]
        if quant_4bit_model:
            self.flags += ["--quant_4bit_model"]

        # SFT flags
        if sft:
            self.flags += ["--sft"]
        if sft_name:
            self.flags += ["--sft_name", sft_name]
        if sft_model_dir:
            raise NotImplementedError("sft_model_dir is not yet implemented in BuchelAdapter")
        if auto_train:
            self.flags += ["--auto_train"]
        if prefer_merged:
            self.flags += ["--prefer_merged"]
        if dataset_path:
            raise NotImplementedError("dataset_path is not yet implemented in BuchelAdapter")
        if dataset:
            self.flags += ["--dataset", dataset]

        self.sft = sft
        # Respect the common adapter device switch. Historically this always
        # inserted gpus="all", so even a runner passing use_gpus=False still
        # exposed GPUs to both the app and its Ollama service.
        # The Buchel code will fail if it does not have GPU access, but that
        # is better than overriding the user's request to not use GPUs.
        requested_use_gpus = kwargs.get("use_gpus")
        if "gpus" in kwargs:
            self.gpus = kwargs["gpus"]
        elif requested_use_gpus is None:
            self.gpus = "all"
        else:
            self.gpus = "all" if requested_use_gpus else False
        kwargs["gpus"] = self.gpus
        if self.gpus is True:
            self.gpus = "all"

        # Track host dirs we create so we can clean them up later
        self._created_host_dirs: list[Path] = []
        self._mounted_finetuning = False
        self._mounted_experiments = False

        binds: list[Tuple[str, str]] = []

        # Default host_output_dir to the caller's CWD
        if host_output_dir is None:
            host_output_dir = Path.cwd().resolve()
        else:
            host_output_dir = Path(host_output_dir).resolve()

        # Resolve engine early so we can probe the image
        engine = (kwargs.get("engine") or os.environ.get("CONTAINER_ENGINE") or "docker").strip().lower()
        if engine not in VALID_ENGINES:
            engine = "docker"

        # Decide mounts, creating host dirs ONLY if we are actually mounting them
        # ---------------------------------------------------------------------
        image_has_sft = False
        if sft and sft_name:
            try:
                image_has_sft = self._image_has_sft(engine, sft_name)
            except Exception as e:
                # Conservative: do NOT shadow image if probe fails
                image_has_sft = True
                if self.verbose:
                    print(f"[BuchelAdapter] WARN: probe failed ({e}); assuming image HAS SFT to avoid shadowing.")
            if self.verbose:
                print(f"[BuchelAdapter] Probe image for SFT '{sft_name}': {'FOUND' if image_has_sft else 'MISSING'}")

        # Rules:
        # - finetuning/output: mount ONLY if image missing SFT AND auto_train enabled (need persistence)
        mount_finetuning = bool(host_output_dir and (not image_has_sft) and auto_train and sft and sft_name)

        # - experiments: mount only if you want container to write into a host-visible experiments folder.
        #   If you don't need it, set this to False.
        mount_experiments = bool(host_output_dir)

        if host_output_dir and mount_experiments:
            experiments_mount = host_output_dir / "experiments"
            experiments_mount.mkdir(parents=True, exist_ok=True)
            self._created_host_dirs.append(experiments_mount)
            binds.append((str(experiments_mount), "/workspace/experiments/"))
            self._mounted_experiments = True
            if self.verbose:
                print("[BuchelAdapter] Mounting host experiments -> /workspace/experiments/")

        if host_output_dir and mount_finetuning:
            finetuning_mount = host_output_dir / "finetuning/output"
            finetuning_mount.mkdir(parents=True, exist_ok=True)
            self._created_host_dirs.append(finetuning_mount)
            binds.append((str(finetuning_mount), "/workspace/finetuning/output"))
            self._mounted_finetuning = True
            if self.verbose:
                print("[BuchelAdapter] Mounting host finetuning/output -> /workspace/finetuning/output (image missing SFT, auto_train enabled).")
        else:
            if self.verbose:
                print("[BuchelAdapter] Not mounting /workspace/finetuning/output (using image contents).")

        if host_hf_cache:
            binds.append((str(Path(host_hf_cache)), "/tmp/huggingface"))

        # Track output dirs / commit logic
        self.host_output_dir = host_output_dir if host_output_dir else None
        self._finetuning_host_dir = (self.host_output_dir / "finetuning/output") if (self.host_output_dir and self._mounted_finetuning) else None
        self._sft_name = sft_name
        self._commit_after_train = bool(auto_train and sft and sft_name)  # only meaningful for training pathways

        # Snapshot before run ONLY if finetuning/output is mounted (otherwise we won't touch host)
        self._pre_run_snapshot = set()
        if self._finetuning_host_dir and self._finetuning_host_dir.exists():
            self._pre_run_snapshot = set(
                p.relative_to(self._finetuning_host_dir).as_posix()
                for p in self._finetuning_host_dir.rglob("*")
                if p.is_file()
            )

        # Make binds visible to BaseAdapter.predict / DockerSession
        kwargs.setdefault("binds", binds)
        kwargs.setdefault("selinux_label", ":Z")
        kwargs.setdefault("use_gpus", bool(self.gpus))

        super().__init__(verbose=verbose, **kwargs)


    # Ollama management - Buchel's helper functions use the Ollama server
    @classmethod
    def _ensure_ollama_running(cls, *, engine: str | None = None, gpus: bool | str = True, verbose: bool = False):
        """Ensure an Ollama server container is up using the chosen engine ("docker" or "podman").
        Engine resolution order: explicit arg -> $CONTAINER_ENGINE -> "docker". We also try to
        add GPU flags appropriately for the selected engine.
        """
        engine = (engine or os.environ.get("CONTAINER_ENGINE") or "docker").strip().lower()
        if engine not in VALID_ENGINES:
            engine = "docker"

        try:
            out = _run([engine, "ps", "-a", "--format", "{{.Names}}	{{.Image}}	{{.Status}}"],
                       capture_output=True, text=True, check=False).stdout
        except Exception as e:
            if verbose:
                print(f"[BuchelAdapter] Couldn't query {engine}: {e}")
            return

        target = None
        status = None
        for line in out.splitlines():
            parts = line.split("	")
            if len(parts) < 3:
                continue
            name, image, st = parts[0], parts[1], parts[2].lower()
            if name == cls._ollama_name or image == cls._ollama_image:
                target = name
                status = st
                break

        if target and ("up" in status or "running" in status or "healthy" in status):
            if verbose:
                print(f"[BuchelAdapter] Ollama already running as {target} (status={status})")
            return

        if target:
            if verbose:
                print(f"[BuchelAdapter] Starting existing Ollama container: {target}")
            _run([engine, "start", target], capture_output=True, check=False)
            return

        # Launch a new container
        cmd = [
            engine, "run", "-d",
            "--name", cls._ollama_name,
            "-p", f"{cls._ollama_port}:11434",
        ]
        if gpus:
            spec = "all" if gpus is True else str(gpus)
            if _gpus_supported(cls._ollama_image, engine, spec):
                cmd += _gpu_args(engine, spec)
            else:
                print(
                    f"[WARN] GPU requested for Buchel/Ollama but unavailable for {engine}; running without GPU.",
                    flush=True,
                )
        cmd += [cls._ollama_image]
        if verbose:
            print(f"[BuchelAdapter] Launching new Ollama container: {' '.join(cmd)}")
        _run(cmd, capture_output=True, check=False)

    @staticmethod
    def _ollama_url_for_engine(engine: str, port: str) -> str:
        if "OLLAMA_API_URL" in os.environ:
            return os.environ["OLLAMA_API_URL"]

        engine = (engine or "").strip().lower()
        if engine == "podman":
            return f"http://host.containers.internal:{port}"
        if engine == "docker":
            return f"http://host.docker.internal:{port}"
        return f"http://127.0.0.1:{port}"


    def build_command(self, in_cn: str, out_cn: str) -> List[str]:
        """Single input: buchel_cli --infile … --outfile … (+ flags)"""
        cmd = [
            self._python_bin, "-u", self.tool_path,
            "--infile", in_cn,
            "--outfile", out_cn,
        ] + list(self.flags)
        if self.verbose:
            print(f"[BuchelAdapter] CMD(single): {' '.join(cmd)}")
        return cmd

    def build_bulk_command(self, in_dir_cn: str, out_dir_cn: str, manifest_cn: str | None) -> List[str] | None:
        """Bulk input: buchel_cli --indir … --outdir … (+ flags)"""
        cmd = [
            self._python_bin, "-u", self.tool_path,
            "--indir", in_dir_cn,
            "--outdir", out_dir_cn,
        ] + list(self.flags)
        if self.verbose:
            print(f"[BuchelAdapter] CMD(bulk): {' '.join(cmd)}")
        return cmd

    def parse_sentences(self, d: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Translate buchel_cli output into the framework sentence schema.
        buchel_cli writes a list with a single item per input, shaped like:
        [ { id, text, strategy, predictions: [{code, TTP}], raw_response } ]
        If document-level mode was used, return an empty sentence list per spec.
        Otherwise, expose one aggregate "sentence" with all predictions.
        """
        if not isinstance(d, list) or not d:
            return []
        item = d[0] if isinstance(d[0], dict) else None
        if not item:
            return []
        strategy = item.get("strategy") or {}
        if bool(strategy.get("document_level")):
            return []
        for prediction in (item.get("predictions") or []):
            code = prediction.get("code")
            if not is_valid_ttp_code(code):
                prediction["code"] = None
        return [{
            "text": item.get("text", ""),
            "ttps": item.get("predictions") or [],
        }]

    def extract_ttps(self, d: Dict[str, Any]) -> List[str]:
        if not isinstance(d, list):
            return []
        seen = set()
        for it in d:
            for p in (it.get("predictions") or []):
                code = p.get("code")
                if is_valid_ttp_code(code):
                    seen.add(code)
        return sorted(seen)

    # Ensure Ollama is up before delegating to BaseAdapter.predict
    def predict(self,
                texts: Iterable[str],
                *,
                ids: Iterable[str] | None = None,
                save_dir: str | Path | None = None,
                prefix: str = "buchel",
                bulk: bool = False,
                ) -> Tuple[List[Dict[str, Any]], List[Path | None]]:
        engine = self.kwargs.get("engine") or os.environ.get("CONTAINER_ENGINE", "docker")

        # Ensure server up (unchanged)
        self._ensure_ollama_running(engine=str(engine), gpus=self.gpus, verbose=bool(self.verbose))

        # Compose env
        env = dict(self.env or {})
        ollama_url = self._ollama_url_for_engine(str(engine), self._ollama_port)
        env.setdefault("OLLAMA_API_URL", ollama_url)
        env.setdefault("OLLAMA_HOST", ollama_url)
        self.env = env
        if self.verbose:
            print(f"[BuchelAdapter] OLLAMA_API_URL={env['OLLAMA_API_URL']}")

        try:
            results = super().predict(texts, ids=ids, save_dir=save_dir, prefix=prefix, bulk=bulk)

            engine_norm = str(engine).strip().lower()
            if self._commit_after_train and self._finetune_outputs_changed():
                if self.verbose:
                    print("[BuchelAdapter] Detected new finetuning outputs; committing image.")
                self._commit_image_with_outputs(engine=engine_norm)
                self._cleanup_host_outputs()

            return results
        finally:
            # Always attempt to remove empty host dirs we created (safe no-op if not empty)
            self._cleanup_host_dirs_if_created()

    
    def _image_has_sft(self, engine: str, sft_name: str) -> bool:
        merged_cfg = f"/workspace/finetuning/output/{sft_name}/merged/config.json"
        adapter_cfg = f"/workspace/finetuning/output/{sft_name}/adapter_config.json"
        cmd = [
            engine, "run", "--rm", self.image,
            "bash", "-lc",
            f"test -f {merged_cfg} -o -f {adapter_cfg}"
        ]
        r = _run(cmd, capture_output=True, text=True, check=False)
        return (r.returncode == 0)


    
    def _finetune_outputs_changed(self) -> bool:
        """Return True if finetuning/output gained any new files since init snapshot."""
        if not self._finetuning_host_dir:
            return False
        post = set(
            p.relative_to(self._finetuning_host_dir).as_posix()
            for p in self._finetuning_host_dir.rglob("*")
            if p.is_file()
        )
        added = post - (self._pre_run_snapshot or set())
        if self.verbose:
            print(f"[BuchelAdapter] finetuning/output new files: {len(added)}")
            for x in sorted(list(added))[:20]:
                print(f"  + {x}")
        return bool(added)

    def _commit_image_with_outputs(self, *, engine: str) -> None:
        """
        Create a one-off container from self.image, copy mounted outputs into the image FS,
        then commit the container back into self.image (or a new tag).
        """
        if not self._finetuning_host_dir:
            return

        # Where the host outputs will be mounted in the one-off container
        host_out = self._finetuning_host_dir.resolve()
        mount_src = str(host_out)
        mount_dst = "/tmp/finetuning_output_ro"

        tmp_name = f"buchel_commit_{os.getpid()}"
        image_in = self.image
        image_out = self.image  # or change to f"{self.image}-with-sft" if you want a new tag

        # Copy artifacts into image FS, then exit
        copy_cmd = (
            "set -euo pipefail; "
            "mkdir -p /workspace/finetuning/output; "
            f"cp -a {mount_dst}/. /workspace/finetuning/output/; "
            "ls -la /workspace/finetuning/output || true"
        )

        run_cmd = [
            engine, "run", "--name", tmp_name,
            "-v", f"{mount_src}:{mount_dst}:ro",
            image_in,
            "bash", "-lc", copy_cmd,
        ]

        if self.verbose:
            print(f"[BuchelAdapter] Commit staging run: {' '.join(run_cmd)}")

        _run(run_cmd, capture_output=not self.verbose, check=True)

        commit_cmd = [engine, "commit", tmp_name, image_out]
        if self.verbose:
            print(f"[BuchelAdapter] Commit: {' '.join(commit_cmd)}")
        _run(commit_cmd, capture_output=not self.verbose, check=True)

        rm_cmd = [engine, "rm", "-f", tmp_name]
        _run(rm_cmd, capture_output=True, check=False)

    def _cleanup_host_outputs(self) -> None:
        """
        Clear host_output_dir finetuning outputs after commit.
        Only acts if we actually mounted finetuning/output.
        """
        if not self._finetuning_host_dir:
            return

        import shutil

        # Remove only the SFT subtree if available; otherwise remove everything under finetuning/output
        target = (self._finetuning_host_dir / self._sft_name) if (self._sft_name) else self._finetuning_host_dir
        if target.exists():
            if self.verbose:
                print(f"[BuchelAdapter] Cleaning host outputs: {target}")
            shutil.rmtree(target, ignore_errors=True)

        # Try to remove now-empty parents we may have created
        # (finetuning/output, finetuning, host_output_dir) — but only if empty
        self._maybe_rmdir(self._finetuning_host_dir)
        self._maybe_rmdir(self._finetuning_host_dir.parent)  # finetuning/

    def _maybe_rmdir(self, p: Path) -> None:
        """Remove directory if empty; ignore errors."""
        try:
            if p.exists() and p.is_dir() and not any(p.iterdir()):
                p.rmdir()
        except Exception:
            pass


    def _cleanup_host_dirs_if_created(self) -> None:
        """
        Remove only directories we created (and only if empty).
        Runs after predict() in a finally block.
        """
        for p in getattr(self, "_created_host_dirs", []):
            self._maybe_rmdir(p)
        # Also attempt to cleanup parent directories if they became empty
        for p in getattr(self, "_created_host_dirs", []):
            self._maybe_rmdir(p.parent)




def predict_texts(texts: List[str],
                  ids: List[str] | None = None,
                  *, save_dir: str | Path | None = None,
                  bulk: bool = False,
                  **kwargs) -> Tuple[List[Dict[str, Any]], List[Path | None]]:
    return BuchelAdapter(**kwargs).predict(texts, ids=ids, save_dir=save_dir, prefix="buchel", bulk=bulk)
