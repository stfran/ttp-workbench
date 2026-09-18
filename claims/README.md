# Claims runner

From the repository root, after `install.sh` completes, run one of:

```bash
bash claims/claims.sh --core
bash claims/claims.sh --optional
bash claims/claims.sh --all
```

Each invocation smoke-tests the selected adapters on the three reports in
`Tests/poc_tests`, runs the matching `Tests/Reproductions` selection, and
regenerates benchmark Figures 4--6 and 8 from saved predictions. Evidence is
stored in a unique directory below `claims/runs/`; `claims/REPORT.md` is
refreshed as the concise entry point to the latest claims run.

Resume the newest interrupted project run without copying its generated path:

```bash
bash claims/claims.sh --resume-latest
```

Core is the modest-resource evaluator path. Optional contains the longer and
resource-sensitive experiments. Use `claims/claims.sh --help` for engine,
device, profile, and logging options.

The implementation exposes the same work as independent phases for the
artifact-evaluation envelope:

```bash
bash claims/claims.sh smoke --scope all --run-dir PATH
bash claims/claims.sh reproduce --scope core --run-dir PATH
bash claims/claims.sh benchmark --run-dir PATH
```

These phase commands are an implementation interface for the external
per-claim scripts. Normal GitHub use should prefer the combined command above.

The evaluator's Claim 1 path adds `--fidelity-smoke` to the standalone smoke
command. This runs the same three inputs through both the framework and native
paths for all ten tools and records the applicable fidelity verdict. Without
that flag, the project runner retains its framework-only default.
