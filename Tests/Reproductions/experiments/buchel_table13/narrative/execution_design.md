## Execution design

Each call expands Zenodo record 16753555's `ext_tools.zip` into its result directory and records the archive, runner, data, model, runtime, and image hashes.

### Without the framework

Each tool's Büchel external-tools environment executes the released `test_attackg_bosch.py` or `test_ladder_bosch.py`. The runner file remains byte-identical to the ZIP. Its expected dataset path is populated, and a return profiler retains predictions without changing the runner, its extraction arguments, or its scoring. The ZIP omits trained weights, so the per-run tree links to the separately staged, hashed model files.

AttacKG retains Büchel's released `max_iter: int = 10000` alignment cap. It also applies the maintained cache-based optimization to repeated NetworkX shortest-path queries and saves it as `shortest_path_cache.diff`. The attack graph is immutable during matching, and the ordered-pair cache is scoped to and discarded with that report graph.

### With the framework

The LADDER adapter starts the standard `ttp-workbench:ladder` image as a disposable `--rm` container. Before inference it compares the selected Büchel files with the code in the image, copies only byte-different files, records every hash and text diff, and verifies the resulting files. The container is stopped and removed after prediction. No image commit occurs, so none of these changes survives.

The AttacKG framework call derives a run-scoped image from `ttp-workbench:attackg`, overlays the byte-different Büchel source and requirements, and installs Python 3.10 plus Büchel's released packages in `/opt/buchel-venv`. It derives bootstrap and supplemental pins from the runtime inventory saved by the preceding native call, installs those pins with the released requirements, and smoke-checks the `coreferee`/`pkg_resources` import path. Before framework inference, it requires the same Python major/minor version and complete installed-distribution version set. The comparison is retained as `environment_comparison.json`.

Each run retains the temporary-image manifest, source and package inventory, source diff, environment comparison, derived Containerfile, Büchel requirements, and native bootstrap and supplemental pins with its AttacKG framework evidence. The manifest records the immutable temporary image ID and whether cleanup completed.

The standard AttacKG image and its Python 3.8 environment are not modified. The temporary derived image is force-removed when the framework call succeeds, fails, or receives the launcher's termination signal; its build and cleanup manifest remains with the run. A process or host failure that prevents Python cleanup can leave a tagged temporary image, which is explicitly detectable by the recorded tag and the manifest's cleanup state.

Framework predictions are scored with a generated replay copy of the released native runner. Only the extractor imports and extractor function are replaced by iteration over the saved predictions; the generated source and diff are retained. Buchel's document grouping, label filtering, metric functions, rounding, and `bosch_scores.txt` output remain in use.

The native and framework AttacKG extraction paths use aligned Python and released package versions but remain separate execution paths: one invokes the released runner from its `.venv`, and the other invokes the adapter in the temporary image. Framework scoring runs in the reproduction environment and is recorded separately because it does not execute extraction. Cached Hugging Face models run in offline mode. Büchel's unconditional NLTK download calls can emit a network error before using the staged local resources.
