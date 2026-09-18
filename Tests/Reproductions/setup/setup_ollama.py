#!/usr/bin/env python3
"""Prepare only the released Buchel embedding service; leave unrelated servers untouched."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
from urllib.parse import urlparse

MODEL = "rjmalagon/gte-qwen2-7b-instruct:f16"
NAME = "ae-reproductions-ollama"
CONTEXT = 32768
SETUP = Path(__file__).resolve().parent
SUITE = SETUP.parent
TOOLS = Path(os.environ.get("REPRO_EXTERNAL_ROOT", SUITE / ".runtime/external_tools")).resolve()


def inspect_container(engine, name=NAME):
    result = subprocess.run([engine, "container", "inspect", name], capture_output=True, text=True)
    if result.returncode:
        return None
    return json.loads(result.stdout)[0]


def context_length(info):
    values = dict(item.split("=", 1) for item in info["Config"].get("Env", []) if "=" in item)
    return int(values.get("OLLAMA_CONTEXT_LENGTH", "0"))


def owns_endpoint(info, url, port, cache):
    endpoint = urlparse(url)
    bindings = info.get("HostConfig", {}).get("PortBindings", {}).get("11434/tcp") or []
    return (endpoint.scheme == "http" and endpoint.hostname in ("localhost", "127.0.0.1")
            and endpoint.port == port
            and any(int(item["HostPort"]) == port for item in bindings)
            and any(Path(item.get("Source", "")).resolve() == cache.resolve()
                    and item.get("Destination") == "/root/.ollama" for item in info.get("Mounts", [])))

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--engine", choices=["podman", "docker"], default="podman")
    p.add_argument("--recreate", action="store_true", help="Explicitly replace a stale AE-owned service; retain the stopped previous container for rollback")
    args = p.parse_args()
    port = int(os.environ.get("OLLAMA_PORT", "11434"))
    url = os.environ.get("AE_OLLAMA_HOST_URL", "http://127.0.0.1:" + str(port))
    context = int(os.environ.get("OLLAMA_CONTEXT_LENGTH", str(CONTEXT)))
    if context <= 0:
        raise ValueError("OLLAMA_CONTEXT_LENGTH must be positive")
    cache = TOOLS / "cache/ollama"
    info = inspect_container(args.engine)
    def tags():
        try:
            with urllib.request.urlopen(url + "/api/tags", timeout=5) as response:
                return [item["name"] for item in json.load(response)["models"]]
        except Exception: return None
    current = tags()
    owned = info is not None and owns_endpoint(info, url, port, cache)
    if (info is not None and not owned) or (info is None and current is not None):
        if args.recreate:
            raise RuntimeError("Refusing --recreate: the requested endpoint is not the AE-owned service/cache")
        if current is None:
            raise RuntimeError("The AE container name is already in use with different mounts/ports; no container was changed")
        if MODEL not in current:
            raise RuntimeError("An existing Ollama service lacks " + MODEL + "; configure it explicitly before RAG. No unrelated server was changed.")
        print("Using external embedding service:", url, MODEL,
              "(context not verified here; configure OLLAMA_CONTEXT_LENGTH explicitly on that service)")
        return
    endpoint = urlparse(url)
    if not owned and not (endpoint.scheme == "http" and endpoint.hostname in ("localhost", "127.0.0.1") and endpoint.port == port):
        raise RuntimeError("Requested external endpoint is unavailable; no local replacement was started")
    stale = owned and context_length(info) != context
    if stale and not args.recreate:
        raise RuntimeError(f"AE Ollama context is {context_length(info) or 'not explicitly set'}, requested {context}. "
                           "Run setup_ollama.py --recreate to replace this owned service while preserving its cache and rollback container.")
    if owned and not stale and current is not None and MODEL in current:
        print("Using AE embedding service:", MODEL, "context=" + str(context))
        return
    cache.mkdir(parents=True, exist_ok=True)
    backup = None
    if stale:
        backup = NAME + "-before-context-" + time.strftime("%Y%m%dT%H%M%S")
        logs = TOOLS / "setup_logs"
        logs.mkdir(exist_ok=True)
        (logs / (backup + ".json")).write_text(json.dumps(info, indent=2))
        subprocess.run([args.engine, "stop", "--time", "30", NAME], check=True)
        subprocess.run([args.engine, "rename", NAME, backup], check=True)
        print("Previous stopped container retained for rollback:", backup, flush=True)
    if owned and not stale:
        subprocess.run([args.engine, "start", NAME], check=True)
    else:
        gpu = ["--device", "nvidia.com/gpu=all"] if args.engine == "podman" else ["--gpus", "all"]
        # A context-only migration keeps exactly the currently installed Ollama image/version.
        image = info["Image"] if stale else "localhost/generation_ollama:latest"
        subprocess.run([args.engine, "run", "-d", "--name", NAME, "-p", str(port) + ":11434", *gpu,
                        "-e", "OLLAMA_CONTEXT_LENGTH=" + str(context),
                        "-v", str(cache) + ":/root/.ollama", "--entrypoint", "ollama",
                        image, "serve"], check=True)
    for _ in range(60):
        current = tags()
        if current is not None: break
        time.sleep(1)
    else: raise RuntimeError("Embedding service did not become ready")
    if MODEL not in current:
        subprocess.run([args.engine, "exec", NAME, "ollama", "pull", MODEL], check=True, timeout=1800)
    if MODEL not in (tags() or []): raise RuntimeError("Embedding model is not ready")
    print("Embedding service ready; context=" + str(context) + "; cache=" + str(cache))

if __name__ == "__main__": main()
