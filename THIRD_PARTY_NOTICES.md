# Third-Party Notices

This file summarizes third-party tools and datasets used by TTP-WorkBench.

## License Scope

Unless otherwise stated, the TTP-WorkBench framework code, adapters, wrapper scripts, Dockerfiles, evaluation scripts, and documentation written by the TTP-WorkBench authors are governed by the license in `LICENSE`.

The common-record schema in `Datasets/schema.json`, benchmark annotations,
labels and structured metadata created by the TTP-WorkBench authors, and the
authors' selection, normalization, and arrangement of the compiled datasets
are governed by `DATA_LICENSE.md`. That license does not apply to third-party
report text or other content retained in compiled records, datasets, models,
or other material that the project authors do not have authority to relicense.

TTP-WorkBench interacts with third-party code, data, models, and public cyber threat reports. Those third-party materials remain governed by their original licenses, terms of use, or copyright status. The inclusion of an adapter, Dockerfile, build script, or compatibility patch in this repository does not relicense any upstream component.

For tools whose upstream licensing status does not clearly permit redistribution, we avoid distributing copied source trees or prebuilt container images. In those cases, the Dockerfile/build script documents how to reconstruct the environment locally from the upstream source. See Docker_Setup for details.

## Data Notices

### MITRE ATT&CK

TTP-WorkBench includes MITRE ATT&CK STIX JSON files under `Framework/utils/attack_stix/` and uses them to validate ATT&CK identifiers, retrieve TTP metadata, and handle updated/deprecated technique codes. The included snapshot is ATT&CK version 19.2 and is distributed under MITRE's license reproduced in `Framework/utils/attack_stix/LICENSE.txt`. Copies of those files must retain MITRE's copyright designation and license.

### Author-labeled cyber threat reports

The benchmark data includes labels derived from public cyber threat reports whose authors included MITRE ATT&CK codes in the report text. The original reports remain the property of their respective publishers unless otherwise licensed. For scientific integrity and reproducibility, we include the report text together with extracted ATT&CK labels, report metadata, source URLs where available, and hashes used to identify reports across experiments.

We include these reports only for research purposes. We do not claim ownership of the underlying report text, and inclusion in this benchmark does not imply endorsement by the original report publisher. 

#### Takedown requests

If you are the rights holder for a report included in this benchmark and would like the report text removed, please open a GitHub issue in the public repository and identify the report title, source URL, file path within the repository, and requested action. We will review good-faith requests and, where appropriate, remove the report text while preserving metadata such as hashes, source URLs, labels, aggregate statistics, and cached evaluation outputs needed to maintain scientific traceability.

## Attribution Statement

TTP-WorkBench builds on and evaluates prior research artifacts from the TTP extraction literature. We thank the authors of the upstream tools, datasets, and models for making their work available for study and reuse. TTP-WorkBench's containerization and adapters are intended to support reproducibility and comparability while preserving attribution to the original works.
