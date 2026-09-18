# Datasets

We curated data from the prior works that we adapted to TTP-WorkBench and also contribute additional ground truth data based on reports whose authors explicitly reference MITRE ATT\&CK. 

## Resources

In the course of our work we needed to consolidate information from other works in a more programmatic fashion. For instance, the authors of AttacKG had reported their ground truth in a word document, so we transferred that information to a json file.

## Generate the Data

The recommended full setup from the repository root builds all containers and
then compiles both dataset trees in dependency order:

```bash
bash install.sh --engine docker
```

To run the data compiler directly after the containers already exist:

```bash
cd Datasets
python compile_data.py
```

For an idempotent setup rerun, `--skip-existing` validates the concrete
compiled-data inventory and exits before accessing any container when it is
complete. `--existing-only` performs the same validation but fails instead of
compiling when outputs are missing; the top-level scoped setup uses this to
avoid silently producing a partial dataset from only core or optional images.

The curated data is extracted from the container of the tool, so you must run the Docker setup first. The data that we contribute is in resources/author_labeled_reviewed.zip. It will be unzipped by the compile_data.py script. 

NOTE: We have commented out all the sentence level compilation because we do not test these in our benchmark tests. 

## Generate the reproduction inputs

The normal `compile_data.py` run also collects the raw reproduction inputs.
The reproduction compiler then converts those inputs into the runner layout:

```bash
python Datasets/compile_data.py \
  --buchel-ext-tools-root Docker_Setup/Buchel/buchel_generation/ext_tools
python Datasets/compile_reproduction_data.py
```

Pass `--skip-existing` to validate and reuse a complete
`Tests/Reproductions/data` tree without rebuilding it in a temporary directory.
The compiler also stages six cropped Orbinato Figure 3
paper panels from `Datasets/resources/orbinato_original_figures`; the Orbinato reproduction
runner places them beside the corresponding direct and framework plots so that results are visually comparable.

## Antivirus and Threat Reports

Cyber Threat Reports can contain literal code samples that resemble
malicious content and may trigger endpoint-protection software. During a validation, an ATT&CK Web Shell entry containing a one-line PHP
web-shell example was removed when it was staged as an individual text file.
`compile_reproduction_data.py` replaces only the observed sample variants with
`[DEFANGED PHP WEB-SHELL EXAMPLE: code omitted]` in the affected rcATT and
SeqMask inputs. The surrounding text, row counts, and ground-truth labels are
preserved, and validation checks the expected replacement counts. This handles
the instance we observed, but other reports may contain different
signature-like text that endpoint protection could flag or quarantine later.

## Schema

The machine-readable common-record schema is in `schema.json`.

- "text": The text of the report. For curated data, we copy the text as provided. For author-labeled reports we have used the text version of the report from archive.orkl.eu/{sha1_hash}.txt
- "sha1_hash": we either compute the sha1 has of the text of the report or reuse the sha1 from orkl.eu
- "title": we reuse the title from curated reports, if provided. Likewise, we copy the title from the orkl.eu metadata if it is available.
- "filepaht": local location of the original file after compile_data.py has ran
- "provenance": the shortname (tool name) of the project from which the data is sourced. 
- "purpose": the main purpose of the data from the original project (e.g. training)
- "source": the root source of the data (either ATT\&CK or other)
- "ground_truth": the ground truth TTP labels for the "text." We copy the ground truth from the prior work data. For the author-labeled data, we manually review.
- "attack_version": the version of ATT\&CK that the ground truth is from if known. For the curated prior work data, we try to infer for the version from reviewing the paper or code. 
- "modality": "report" or "sentence" level data

Optional:

- "reviewed": Indicates the file has been manually reviewed in our study. This only applies to the author-labeled reports
- "rejected": This indicates the report was rejected during manual review due to inaccuracies in the ground truth. This only applies to author_labeled_reviewed reports and none are present as we have discarded them from dataset.
