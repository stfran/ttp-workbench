#!/usr/bin/env python3
"""Stage pinned tested checkouts, supplied models, native environments and images.

Stages are explicit; no reproduction runner calls this installer. Image updates
preserve a rollback tag and use stopped staging containers, never running jobs.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

SETUP = Path(__file__).resolve().parent
SUITE = SETUP.parent
PROJECT = SUITE.parents[1]
TOOLS = Path(os.environ.get("REPRO_EXTERNAL_ROOT", SUITE / ".runtime/external_tools")).resolve()
SPECS = {
    "Orbinato": ("orbinato", "a8cacf3185d098c686e0d88768a619a03a4d76d1", "3.10"),
    "rcATT": ("rcatt", "f82f7fd456279abefcd3e0b50e8056345c11aeb7", "3.7"),
    "RAF-AG": ("raf-ag", "f2868edc1be6a09fc51b1d57907799602ddaa0eb", "3.9"),
    "SeqMask": ("seqmask", "f3686599065a58927562fbb5c4bf71c075a687ba", "3.9"),
}


def call(*args, **kw):
    print("+", " ".join(map(str, args)), flush=True)
    try:
        return subprocess.check_output(list(map(str, args)), text=True, **kw).strip()
    except subprocess.CalledProcessError as exc:
        print((exc.output or "")[-12000:], flush=True)
        raise


def stage_models(destination):
    source = PROJECT / "Docker_Setup/Orbinato/private_models"
    if not (source / "secbert_model/trained_secbert.pt").is_file():
        raise RuntimeError("Stage the private model archive before Orbinato setup")
    for path in source.rglob("*"):
        if path.is_file() and not path.is_symlink():
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.read_bytes() == path.read_bytes():
                continue
            if target.exists():
                raise RuntimeError("Refusing to replace changed Orbinato model: " + str(target))
            shutil.copy2(path, target)
        for name in ("trained_secbert.pt", "secbert_labels.txt"):
            link = destination / name
            relative = Path("secbert_model") / name
            if link.is_symlink() and os.readlink(link) == str(relative):
                continue
            if link.exists() or link.is_symlink():
                backup = destination / (name + ".before-ae-models")
                if backup.exists():
                    raise RuntimeError("Existing model alias backup: " + str(backup))
                link.rename(backup)
            link.symlink_to(relative)


def environment_steps(name, python, root):
    """Keep these ordered lists aligned with each Dockerfile's Python RUN steps.

    The image freeze is an inventory only, never an installation specification.
    Host OS libraries are supplied by the local Python runtime, not apt.
    """
    pip = [str(python), "-m", "pip"]
    nltk = [str(python), "-m", "nltk.downloader", "-d", str(TOOLS / "cache/nltk")]
    setup = PROJECT / "Docker_Setup" / name
    if name == "rcATT":
        return [pip + ["install", "--upgrade", "pip", "setuptools", "wheel"],
                pip + ["install", "-r", str(setup / "requirements.txt")],
                nltk + ["punkt", "stopwords", "wordnet"],
                [str(python), str(root / "patch_bulk.py"), str(root)]]
    if name == "SeqMask":
        return [pip + ["install", "numpy<2", "scipy<1.11", "joblib==1.3.2", "nltk==3.6.7", "Cython<3"],
                pip + ["install", "--no-build-isolation", "gensim==3.8.3"],
                pip + ["install", "-r", str(setup / "requirements.txt")],
                nltk + ["punkt_tab", "wordnet", "punkt", "omw-1.4"],
                # Evaluation-only dependency, after all Dockerfile steps.
                pip + ["install", "scikit-learn==1.3.2"]]
    if name == "RAF-AG":
        return [pip + ["install", "--upgrade", "pip", "setuptools<81", "wheel"],
                pip + ["install", "-r", str(root / "requirements.txt")],
                pip + ["install", "--no-cache-dir", "nvidia-cuda-nvcc-cu12==12.8.93"],
                pip + ["uninstall", "-y", "numpy"],
                pip + ["install", "numpy<2.0"],
                pip + ["install", "--force-reinstall", "--no-deps", "thinc==8.1.12", "spacy==3.5.0", "fire"],
                [str(python), "-m", "spacy", "download", "en_core_web_lg"],
                [str(python), "-m", "spacy", "download", "en_core_web_trf"],
                pip + ["install", "coreferee"],
                [str(python), "-m", "coreferee", "install", "en"]]
    return [pip + ["install", "--upgrade", "pip", "setuptools", "wheel"],
            pip + ["install", "-r", str(root / "requirements.txt")],
            nltk + ["punkt", "wordnet", "omw-1.4", "stopwords"],
            # Keep Orbinato on a CUDA-12-compatible PyTorch pair.  Unpinned
            # installs can currently select CUDA 13 wheels that older drivers
            # cannot initialize.
            pip + ["install", "transformers<5", "torch==2.6.0", "torchvision==0.21.0"]]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage", action="store_true")
    ap.add_argument("--images", action="store_true")
    ap.add_argument("--environments", action="store_true")
    ap.add_argument("--reset-venv", action="store_true", help="Move the existing .venv to a timestamped backup before replaying Dockerfile installation steps")
    ap.add_argument("--only", choices=SPECS)
    ap.add_argument("--engine", default=os.environ.get("CONTAINER_ENGINE", "podman"))
    args = ap.parse_args()
    if not (args.stage or args.images or args.environments):
        ap.error("Select --stage, --images and/or --environments")
    for name, (tag, commit, python) in SPECS.items():
        if args.only and args.only != name:
            continue
        root = TOOLS / name
        image = "ttp-workbench:" + tag
        if args.stage:
            if not root.exists():
                cid = call(args.engine, "create", image, "/bin/true")
                try:
                    call(args.engine, "cp", cid + ":/opt/" + name, root)
                finally:
                    call(args.engine, "rm", cid)
            actual = call("git", "-C", root, "rev-parse", "HEAD")
            if actual != commit:
                raise RuntimeError("Source revision mismatch: " + name + " " + actual)
            (root / "AE_SOURCE.json").write_text(json.dumps(dict(commit=commit, source_image=image, python=python), indent=2) + "\n")
            if name == "Orbinato":
                stage_models(root / "src")
            if name == "rcATT":
                for filename in ("patch_bulk.py", "rcatt_bulk.py"):
                    shutil.copy2(PROJECT / "Docker_Setup/rcATT" / filename, root / filename)
                call(sys.executable, root / "patch_bulk.py", root)
            if name == "RAF-AG":
                patcher = PROJECT / "Docker_Setup/RAF-AG/patch_determinism.py"
                shutil.copy2(PROJECT / "Docker_Setup/RAF-AG/matcher.py", root / "matcher.py")
                shutil.copy2(patcher, root / patcher.name)
                call(sys.executable, root / patcher.name, root)
            # Record exact tested package versions; native Python remains isolated.
            frozen = call(args.engine, "run", "--rm", image, "/opt/venv/bin/python", "-m", "pip", "freeze")
            (root / "ae_requirements.txt").write_text(frozen + "\n")
        if args.images:
            # Guard source/model staging before touching the image tag.
            if not (root / "AE_SOURCE.json").exists():
                raise RuntimeError("Run --stage first: " + name)
            backup = image + "-before-optional-ae"
            if subprocess.run([args.engine, "image", "exists", backup]).returncode:
                call(args.engine, "tag", image, backup)
            command = ["/opt/venv/bin/python", "-m", "pip", "install", "transformers<5"] if name == "Orbinato" else ["/bin/true"]
            cid = call(args.engine, "create", image, *command)
            try:
                if name == "Orbinato":
                    call(args.engine, "start", "--attach", cid)
                    # Install only archive-provided models; preserve tested code.
                    with tempfile.TemporaryDirectory(prefix="orbinato-models-") as tmp:
                        stage_models(Path(tmp))
                        call(args.engine, "cp", str(Path(tmp)) + "/.", cid + ":/opt/Orbinato/src/")
                elif name == "rcATT":
                    for filename in ("rcATT_cmd.py", "classification_tools/__init__.py", "rcatt_bulk.py"):
                        call(args.engine, "cp", root / filename, cid + ":/opt/rcATT/" + filename)
                elif name == "SeqMask":
                    call(args.engine, "cp", PROJECT / "Docker_Setup/SeqMask/seqmask_cli.py", cid + ":/opt/SeqMask/seqmask_cli.py")
                elif name == "RAF-AG":
                    call(args.engine, "cp", PROJECT / "Docker_Setup/RAF-AG/matcher.py", cid + ":/opt/RAF-AG/matcher.py")
                call(args.engine, "commit", cid, image)
                if name == "Orbinato":
                    with tempfile.TemporaryDirectory(prefix="orbinato-verify-") as tmp:
                        call(args.engine, "cp", cid + ":/opt/Orbinato/src/.", tmp)
                        private = PROJECT / "Docker_Setup/Orbinato/private_models"
                        for expected in private.rglob("*"):
                            if expected.is_file() and not expected.is_symlink():
                                actual = Path(tmp) / expected.relative_to(private)
                                if not actual.is_file() or actual.read_bytes() != expected.read_bytes():
                                    raise RuntimeError("Container model mismatch: " + str(expected))
                    print("Verified every supplied Orbinato model in the image", flush=True)
            finally:
                call(args.engine, "rm", cid)
        if args.environments:
            runtime = root / ".python"
            executable = runtime / "bin/python"
            if not executable.exists():
                conda = os.environ.get("CONDA_EXE") or shutil.which("mamba") or shutil.which("conda")
                if not conda:
                    raise RuntimeError("Set CONDA_EXE or install conda/mamba")
                call(conda, "create", "-y", "--override-channels", "-c", "conda-forge", "--prefix", runtime, "python=" + python, "pip")
            env = root / ".venv"
            if args.reset_venv and env.exists():
                backup = root / (".venv.before-docker-order-" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
                env.rename(backup)
                print("Previous environment retained:", backup, flush=True)
            if not (env / "bin/python").exists():
                call(executable, "-m", "venv", env)
            child_env = dict(os.environ, PIP_CACHE_DIR=str(TOOLS / "cache/pip"), NLTK_DATA=str(TOOLS / "cache/nltk"))
            steps = environment_steps(name, env / "bin/python", root)
            log_path = TOOLS / "setup_logs" / ("optional_" + name + "_ordered.log")
            log_path.parent.mkdir(exist_ok=True)
            with log_path.open("a") as log:
                for command in steps:
                    print("+", " ".join(command), flush=True)
                    log.write("\n+ " + " ".join(command) + "\n")
                    log.flush()
                    subprocess.run(command, cwd=str(root), env=child_env, stdout=log, stderr=subprocess.STDOUT, check=True)
            (root / "AE_ENVIRONMENT.json").write_text(json.dumps(dict(source="Docker_Setup/" + name + "/Dockerfile", steps=steps), indent=2) + "\n")


if __name__ == "__main__":
    main()
