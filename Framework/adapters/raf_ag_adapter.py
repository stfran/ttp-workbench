# adapters/RAFAG_adapter.py
from __future__ import annotations
from typing import List, Dict, Any, Tuple
from pathlib import Path
import json
from collections import defaultdict

from Framework.adapters.base_adapter import BaseAdapter
from Framework.adapters.docker_session import DockerSession


class RAFAGAdapter(BaseAdapter):
    image         = "ttp-workbench:raf-ag"
    env           = {"PYTHONHASHSEED": "0"}
    _root         = "/opt/RAF-AG"
    _in_dir       = f"{_root}/data/campaign/input"
    _dec_dir      = f"{_root}/data/campaign/decoding_result"
    _pro_dir    = f"{_root}/data/campaign/procedure_alignment"
    _seq_dir      = f"{_root}/data/campaign/sequence_techniques"
    _tech_dir    = f"{_root}/data/campaign/tech_alignment"
    _out_dir = f"{_root}/data/campaign/output"
    _entry_script = f"{_root}/main.py"
    _matcher = f"{_root}/matcher.py" # gets sentence level prediction from main.py output files

    def __init__(self, *, verbose=False, memory:str = None, section: str = "best", **kwargs):
        super().__init__(verbose=verbose, memory=memory, **kwargs)
        self.section = section
        # BaseAdapter passes the adapter explicitly to monitor callbacks.
        self.kwargs.setdefault("bulk_monitor", type(self)._rafag_external_monitor)
        # CPU is the portable default. Callers that have a CUDA-complete image
        # can opt in explicitly with use_gpus=True/require_gpus=True.
        self.kwargs.setdefault("use_gpus", False)
    
    def build_command(self, in_cn: str, out_cn: str):
        shell = f'''
            set -eo pipefail
            export TOKENIZERS_PARALLELISM=false
            export PYTHONUNBUFFERED=1

            rm -rf "{self._in_dir}" "{self._out_dir}" "{self._dec_dir}" "{self._pro_dir}" "{self._seq_dir}" "{self._tech_dir}" # ← clean sample files out
            mkdir "{self._in_dir}" "{self._out_dir}" "{self._dec_dir}" "{self._pro_dir}" "{self._seq_dir}" "{self._tech_dir}" 

            mv "{in_cn}" "{self._in_dir}/$(basename "{in_cn}")"
            cd "{self._root}"

            /opt/venv/bin/python -u "{self._entry_script}"

            # pick ONE .jsonl from output
            jl=$(ls -1 "{self._out_dir}"/*.jsonl 2>/dev/null | head -n1 || true)
            if [ -z "$jl" ]; then
                echo "RAF-AG produced no JSONL in {self._out_dir}" >&2
                ls -l "{self._out_dir}" >&2 || true
                exit 1
            fi

            # pick ONE .json from decoding_result
            dec=$(ls -1 "{self._dec_dir}"/*.json 2>/dev/null | head -n1 || true)
            if [ -z "$dec" ]; then
                echo "RAF-AG produced no JSON in {self._dec_dir}" >&2
                ls -l "{self._dec_dir}" >&2 || true
                exit 1
            fi

            # run your matcher with absolute path and write the final bundle to {out_cn}
            /opt/venv/bin/python -u "{self._matcher}" \
                --decoding_dir "{self._dec_dir}" \
                --jsonl_dir    "{self._out_dir}" \
                --section      "{self.section}" \
                --out          "{out_cn}"
        '''
        return ["/bin/bash", "-lc", shell]
    
    def build_bulk_command(self, in_dir_cn: str, out_dir_cn: str, manifest_cn: str | None) -> List[str]:
        shell = r"""
            set -eo pipefail
            export TOKENIZERS_PARALLELISM=false
            export PYTHONUNBUFFERED=1

            echo "[rafag] preparing campaign folders"
            rm -rf "__IN_DIR__" "__OUT_DIR__" "__DEC_DIR__" "__PRO_DIR__" "__SEQ_DIR__" "__TECH_DIR__"
            mkdir  "__IN_DIR__" "__OUT_DIR__" "__DEC_DIR__" "__PRO_DIR__" "__SEQ_DIR__" "__TECH_DIR__"

            echo "[rafag] staging inputs from __IN_DIR_CN__ -> __IN_DIR__"
            shopt -s nullglob
            cp -f "__IN_DIR_CN__"/*.txt "__IN_DIR__/"
            total=$(find "__IN_DIR__" -maxdepth 1 -type f -name '*.txt' | wc -l)
            echo "[rafag] total inputs=$total"

            cd "__ROOT__"
            echo "[rafag] launching main.py"
            /opt/venv/bin/python -u "__ENTRY__" &
            pid=$!

            # ── live progress ──────────────────────────────────────
            while kill -0 "$pid" 2>/dev/null; do
            dec_count=$(find "__DEC_DIR__" -maxdepth 1 -type f -name '*.json' | wc -l)
            out_count=$(find "__OUT_DIR__" -maxdepth 1 -type f -name '*.jsonl' | wc -l)
            echo "[rafag][progress] decoded=$dec_count/$total  jsonl=$out_count/$total"
            sleep 15
            done
            wait "$pid"
            echo "[rafag] main.py finished"

            # ── combine per document using matcher.py  ─────────────
            mkdir -p "__OUT_DIR_CN__"
            python3 - <<'PY'
    import os, json, glob, subprocess, pathlib, sys
    root = "__ROOT__"
    dec_dir = "__DEC_DIR__"
    jl_dir  = "__OUT_DIR__"
    out_dir = "__OUT_DIR_CN__"
    manifest_path = "__MANIFEST_CN__" if __HAS_MANIFEST__ else None
    section = "__SECTION__"

    # Map base filename -> desired out filename from manifest
    name_map = {}
    if manifest_path and os.path.exists(manifest_path):
        with open(manifest_path, 'r', encoding='utf-8') as fh:
            man = json.load(fh)
        for m in man:
            base = os.path.splitext(os.path.basename(m['in']))[0]
            name_map[base] = m['out']

    pathlib.Path(out_dir).mkdir(parents=True, exist_ok=True)

    # Try to run matcher once per produced pair. If matcher doesn't support filtering,
    # it should still write something; we warn otherwise.
    produced = 0
    for dec in sorted(glob.glob(os.path.join(dec_dir, '*.json'))):
        base = os.path.splitext(os.path.basename(dec))[0]
        jl = os.path.join(jl_dir, base + '.jsonl')
        if not os.path.exists(jl):
            print(f"[rafag][warn] no jsonl for {base}", file=sys.stderr)
            continue
        out_name = name_map.get(base, base + '.json')
        target   = os.path.join(out_dir, out_name)
        cmd = [
            "/opt/venv/bin/python", "-u", f"{root}/matcher.py",
            "--decoding_dir", dec_dir,
            "--jsonl_dir", jl_dir,
            "--section", section,
            "--out", target,
            "--only_base", base,
        ]
        try:
            subprocess.check_call(cmd)
            produced += 1
        except subprocess.CalledProcessError as e:
            print(f"[rafag][warn] matcher failed for base={base}; exit={e.returncode}", file=sys.stderr)

    if produced == 0:
        target = os.path.join(out_dir, 'combined.json')
        try:
            subprocess.check_call([
                "/opt/venv/bin/python", "-u", f"{root}/matcher.py",
                "--decoding_dir", dec_dir,
                "--jsonl_dir", jl_dir,
                "--section", section,
                "--out", target,
            ])
            print("[rafag][warn] wrote combined output only (per-file split unavailable)", file=sys.stderr)
        except Exception as e:
            print(f"[rafag][error] combined matcher call failed: {e}", file=sys.stderr)
    PY
        """

        shell = (shell
            .replace("__IN_DIR__", self._in_dir)
            .replace("__OUT_DIR__", self._out_dir)
            .replace("__DEC_DIR__", self._dec_dir)
            .replace("__PRO_DIR__", self._pro_dir)
            .replace("__SEQ_DIR__", self._seq_dir)
            .replace("__TECH_DIR__", self._tech_dir)
            .replace("__ROOT__", self._root)
            .replace("__ENTRY__", self._entry_script)
            .replace("__IN_DIR_CN__", in_dir_cn)
            .replace("__OUT_DIR_CN__", out_dir_cn)
            .replace("__MANIFEST_CN__", manifest_cn or "")
            .replace("__SECTION__", self.section)
            .replace("__HAS_MANIFEST__", "True" if manifest_cn else "False")
        )
        # The embedded Python and its heredoc terminator share four spaces.
        # Strip those consistently so bash recognizes the terminator and Python
        # receives top-level statements rather than an unexpected indentation.
        import textwrap
        return ["/bin/bash", "-lc", textwrap.dedent(shell)]
    
    # We added a matcher.py file on the host that combines the output and decoded result to get sentences
    def parse_sentences(self, d: List[Dict[str,Any]]) -> List[Dict[str,Any]]:
        by_idx = defaultdict(lambda: {"text": "", "ttps": []})
        for r in d:
            si = r["sentence_index"]
            by_idx[si]["text"] = r["sentence_text"]
            by_idx[si]["ttps"].append({"label": r["technique_id"], "prob": r["confidence"]})
        # stable order by sentence index
        return [by_idx[k] for k in sorted(by_idx)]

    def extract_ttps(self, d: Dict[str, Any]) -> Dict[str, Any]:
        # keep only the “best” predictions block
        return sorted({r["technique_id"] for r in d if r.get("technique_id")})
    
    def _rafag_external_monitor(self, ds:DockerSession, cmd: list[str], total: int,
                            inactivity_timeout_s: int | None,
                            crash_log: Path, soft_fail: bool, verbose: bool) -> None:
        """
        Run RAF-AG bulk command while monitoring its stdout from the HOST.
        Parses lines like 'start decoding this report <FILENAME>' to drive a tqdm progress bar.
        """
        import re, time, threading, subprocess
        from tqdm import tqdm

        # Build 'docker|podman exec ... /bin/bash -lc "<script>"'
        exec_cmd = ds._build_exec_cmd(cmd, workdir=self.workdir, env=self.env)

        # Start the process with line-buffered pipes
        p = ds._start_process(exec_cmd, stdin=None, process_env=ds._exec_process_env(self.env))

        # Reader threads that also feed the inactivity watchdog queue
        q_activity, stdout_buf, stderr_buf, state, t_out, t_err = ds._start_stream_threads(p, tee=verbose)

        # Run inactivity watchdog on a helper thread
        timed_out_flag = {"v": False}
        def _watchdog():
            timed_out_flag["v"] = ds._monitor_process(p, q_activity, inactivity_timeout_s)
        th = threading.Thread(target=_watchdog, daemon=True)
        th.start()

        # Parse RAF-AG progress
        re_done   = re.compile(r"^done\s*$", re.I)
        re_report = re.compile(r"^start\s+decoding\s+this\s+report\s+(\S+)")

        seen = set()
        pbar = tqdm(total=total, unit="report", desc="RAF-AG", disable=not verbose)
        cursor = 0
        try:
            while True:
                # consume any new stdout lines captured by the reader thread
                if cursor < len(stdout_buf):
                    new = stdout_buf[cursor:]
                    cursor = len(stdout_buf)
                    for line in new:
                        m = re_report.search(line)
                        if m:
                            name = m.group(1)
                            if name not in seen:
                                seen.add(name)
                                pbar.update(1)
                                # shows the most recent file hint on the right
                                pbar.set_postfix_str(name[:48])
                        elif re_done.search(line):
                            pbar.refresh()
                if p.poll() is not None:
                    break
                time.sleep(0.25)
        finally:
            pbar.close()
            th.join(timeout=0.5)

        # Finish like DockerSession.exec: collect buffers and raise on failure/timeout
        stdout, stderr = ds._collect_output(p, t_out, t_err, stdout_buf, stderr_buf)
        if timed_out_flag["v"]:
            ds._dump_crash_log(exec_cmd,
                            f"INACTIVITY TIMEOUT after {inactivity_timeout_s}s",
                            stdout, stderr,
                            state.get("last_out", ""), state.get("last_err", ""),
                            crash_log)
            if soft_fail:
                return
            raise subprocess.CalledProcessError(124, exec_cmd, output=stdout, stderr=stderr)

        if p.returncode != 0:
            if soft_fail:
                ds._dump_crash_log(exec_cmd,
                                f"NON-ZERO EXIT {p.returncode}",
                                stdout, stderr,
                                state.get("last_out", ""), state.get("last_err", ""),
                                crash_log)
                return
            raise subprocess.CalledProcessError(p.returncode, exec_cmd, output=stdout, stderr=stderr)



def predict_texts(texts: List[str],
                  ids: List[str] | None = None,
                  *, save_dir: str | Path | None = None,
                  **kw) -> Tuple[List[Dict[str, Any]], List[Path | None]]:
    return RAFAGAdapter(**kw).predict(texts, ids=ids, save_dir=save_dir, prefix="rafag")
