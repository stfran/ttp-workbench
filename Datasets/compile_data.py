#compile_data.py
'''
    unify the data from the reproduced papers into a single JSON format
    - "text" is plain text of report or sentence
    - "sha1_hash" for unique identifier of the report or sentence
    - "title" of the report or document      
    - "filepath" within this project directory          
    - "provenance" of the data links to the adapter, and the paper that tested the data in an
    - "source" of the data indicates the manner of collecting (ATT&CK, manual, other)
    - "purpose" of the data in the original context (e.g. "training") so we can avoid bias  
    - "ground_truth" is the list of original ground truth labels for the 'text'
    - "attack_version" of the labeles if known
    - "modality" of 'report' indicates 'text' is an unabridged report from the source, 'sentence' is a subset of the source report
    track provenance and ground truth 
'''
import argparse
import json, hashlib, csv, sys, os
from pathlib import Path
import pandas as pd
from itertools import chain
import requests
import zipfile
import tqdm
import subprocess

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Framework.adapters.docker_session import DockerSession
from Framework.utils.attack_lookup import TTP_CODE_PATTERN
from Framework.utils.config import adapter_container_map, adapter_workspace_map

BASE_DIR = Path(__file__).resolve().parents[0]
PRIOR_WORK = BASE_DIR / "prior_work_data"
REPORTS_DIR = BASE_DIR / "curated_reports"
DUPLICATES = REPORTS_DIR / "duplicates"
RESOURCES = BASE_DIR / "resources"

REPORTS_DIR.mkdir(parents=True, exist_ok=True)
PRIOR_WORK.mkdir(parents=True, exist_ok=True)
DUPLICATES.mkdir(parents=True, exist_ok=True)
DUP_INDEX_FILE = BASE_DIR / "duplicates_index.json"

ENGINE = os.environ.get("CONTAINER_ENGINE", "podman")
UNFETTER_DICT_SHA256 = "138c817edb755526d8bd9558982095afddeab4e6ca454d2714994cd8bcdf3bfc"

# Track what we've written this run (hash -> path); survives only for this process.
_seen: dict[str, Path] = {}


def compilation_output_issues() -> list[str]:
    """Return concrete reasons the complete compiled dataset cannot be reused."""
    issues: list[str] = []
    expected_json_counts = {
        REPORTS_DIR: 426,
        BASE_DIR / "author_labeled_reviewed": 281,
    }
    for directory, expected in expected_json_counts.items():
        actual = len(list(directory.glob("*.json"))) if directory.is_dir() else 0
        if actual != expected:
            issues.append(f"{directory}: expected {expected} JSON files, found {actual}")

    required = [
        PRIOR_WORK / "AttacKG/Cobalt Campaign.txt",
        PRIOR_WORK / "Buchel/bosch_cti_test_ds.json",
        PRIOR_WORK / "Buchel/bosch_test.json",
        PRIOR_WORK / "Buchel/test_split.json",
        PRIOR_WORK / "LADDER/LADDER_table_9_data/cadelspy.txt",
        PRIOR_WORK / "Orbinato/document_data.py",
        PRIOR_WORK / "RAF-AG/Dataset.zip",
        PRIOR_WORK / "SeqMask/TTPDrill-subTTP.csv",
        PRIOR_WORK / "SeqMask/data_origin13.csv",
        PRIOR_WORK / "SeqMask/data_origin4.csv",
        PRIOR_WORK / "TTP-LLM/MITRE_Procedures.csv",
        PRIOR_WORK / "TTP-LLM/MITRE_Procedures_encoded.csv",
        PRIOR_WORK / "rcATT/dict_wiki",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        issues.append("missing prior-work inputs: " + ", ".join(missing))

    count_checks = [
        (PRIOR_WORK / "TRAM/mjson_files", "*.mjson", 151),
        (PRIOR_WORK / "RAF-AG/Dataset/CTI reports", "*.txt", 30),
        (PRIOR_WORK / "AttacKG", "*.txt", 9),
        (PRIOR_WORK / "LADDER/LADDER_table_9_data", "*.txt", 5),
        (PRIOR_WORK / "LADDER/LADDER_table_9_data", "*.json", 5),
    ]
    for directory, pattern, expected in count_checks:
        actual = len(list(directory.glob(pattern))) if directory.is_dir() else 0
        if actual != expected:
            issues.append(f"{directory}: expected {expected} {pattern} files, found {actual}")
    return issues


def compute_sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()

def _write_json(path: Path, obj: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=4, ensure_ascii=False)

def _load_dup_index() -> dict:
    if DUP_INDEX_FILE.exists():
        try:
            return json.loads(DUP_INDEX_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def _save_dup_index(idx: dict) -> None:
    _write_json(DUP_INDEX_FILE, idx)

def _labels_set(obj: dict) -> set[str]:
    # 'labels' is already canonical/mapped per your clean(..., True).
    return set(obj.get("labels", []) or [])

def _ensure_index_entry(idx: dict, sha1_hash: str, main_file: str) -> dict:
    entry = idx.get(sha1_hash)
    if not entry:
        entry = {
            "main_file": main_file,     # path to the first copy in reports/
            "label_mismatch": False,    # flips to True if any duplicate differs in labels
            "duplicates": {}            # provenance -> info
        }
        idx[sha1_hash] = entry
    return entry

def save_report_with_dedup(report: dict):
    """
    Save report JSON with de-duplication based on sha1_hash and provenance.
    We don't keep it if it's a complete duplicate (same sha1, provenance)

    """
    sha1_hash  = report["sha1_hash"]
    provenance = report["provenance"]

    main_path = REPORTS_DIR / f"{sha1_hash}.json"

    # First time we've seen this sha1: write main file and record it
    if sha1_hash not in _seen and not main_path.exists():
        first_doc = dict(report)
        first_doc.setdefault("duplicate", False)
        _write_json(main_path, first_doc)
        _seen[sha1_hash] = main_path
        return

    # Load the existing report (from disk if present, else from _seen map)
    if main_path.exists():
        first = json.loads(main_path.read_text(encoding="utf-8"))
        first_path_str = str(main_path)
    else:
        first_path = _seen[sha1_hash]
        first = json.loads(first_path.read_text(encoding="utf-8"))
        first_path_str = str(first_path)

    # Only save a duplicate if PROVENANCE differs
    first_prov = first.get("provenance", "UNKNOWN")
    if provenance == first_prov:
        return

    # Compare labels for index flag
    first_labels = _labels_set(first)
    this_labels  = _labels_set(report)
    labels_diff  = (this_labels != first_labels)

    # Path for this duplicate in DUPLICATES_DIR
    safe_prov = provenance.replace("/", "_")
    dup_path  = DUPLICATES / f"{sha1_hash}_{safe_prov}.json"

    # Write duplicate with flag
    dup_doc = dict(report)
    dup_doc["duplicate"] = True
    if "original_file" not in dup_doc:
        dup_doc["original_files"] = [first_path_str]
    else:
        dup_doc["original_files"].append(first_path_str)    
    _write_json(dup_path, dup_doc)

    # Update duplicates index at BASE_DIR
    idx   = _load_dup_index()
    entry = _ensure_index_entry(idx, sha1_hash, main_file=first_path_str)

    entry["label_mismatch"] = entry.get("label_mismatch", False) or labels_diff
    entry.setdefault("duplicates", {})
    entry["duplicates"][provenance] = {
        "original_filename": report.get("filepath", ""),
        "labels": sorted(this_labels),
        "duplicate_file": str(dup_path),
    }

    _save_dup_index(idx)


def load_json(filename):
    with open(filename, 'r', encoding='utf-8') as f:
        json_obj = json.load(f)
    return json_obj


def copy_from_container(adapter_name: str, src_path: str, dest_path: str) -> None:
    # copy the data we need from a given repo's container to the host
    container_name = adapter_container_map.get(adapter_name)
    workspace_path = adapter_workspace_map.get(adapter_name)
    if container_name is None or workspace_path is None:
        raise ValueError(f"Unknown adapter name: {adapter_name}")
    # use cp_dir_from if src_path is a directory (no extension), else use cp_from
    if Path(src_path).suffix == "":
        with DockerSession(container_name, engine=ENGINE) as container:
            container.cp_dir_from(src_path, dest_path)
    else:
        with DockerSession(container_name, engine=ENGINE) as container:
            container.cp_from(src_path, dest_path)

def download_file(url: str, dest_path: Path) -> None:
    response = requests.get(url)
    response.raise_for_status()  # raise an error for bad responses
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_path, 'wb') as f:
        f.write(response.content)


def collect_reproduction_sources(buchel_ext_tools_root: Path, *, include_shared: bool = True) -> None:
    """
    Collect the data for the Tests/Reproductions runs. Pull from the images built by Docker_Setup, the staged Buchel release, and Datasets/resources.
    """
    copies = {
        "AttacKG": [
            ("Dataset/Evaluation", PRIOR_WORK / "AttacKG", True),
        ],
        "Orbinato": [
            ("src/apt_documents", PRIOR_WORK / "Orbinato/apt_documents", True),
            ("src/document_data.py", PRIOR_WORK / "Orbinato/document_data.py", False),
        ],
        "RAF-AG": [
            ("Dataset.zip", PRIOR_WORK / "RAF-AG/Dataset.zip", False),
        ],
        "SeqMask": [
            ("datas", PRIOR_WORK / "SeqMask", True),
        ],
        "TTP-LLM": [
            ("data/MITRE_Procedures.csv",
             PRIOR_WORK / "TTP-LLM/MITRE_Procedures.csv", False),
            ("data/MITRE_Procedures_encoded.csv",
             PRIOR_WORK / "TTP-LLM/MITRE_Procedures_encoded.csv", False),
        ],
        "Buchel": [
            ("finetuning/cti_datasets/bosch/bosch_cti_test_ds.json",
             PRIOR_WORK / "Buchel/bosch_cti_test_ds.json", False),
            ("finetuning/cti_datasets/tram/official_sok_split/test_split.json",
             PRIOR_WORK / "Buchel/test_split.json", False),
        ],
    }
    # A normal compilation has already collected the sources below for the
    # benchmark dataset. The focused mode collects them because it skips the
    # benchmark compilation entirely.
    shared = {
        ("AttacKG", "Dataset/Evaluation"),
        ("Orbinato", "src/apt_documents"),
        ("RAF-AG", "Dataset.zip"),
        ("SeqMask", "datas"),
        ("TTP-LLM", "data/MITRE_Procedures.csv"),
    }
    for adapter, entries in copies.items():
        selected = [
            entry for entry in entries
            if include_shared or (adapter, entry[0]) not in shared
        ]
        if not selected:
            continue
        workspace = Path(adapter_workspace_map[adapter])
        with DockerSession(adapter_container_map[adapter], engine=ENGINE) as container:
            for relative, destination, is_directory in selected:
                source = workspace / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                print(f"Copying {adapter} reproduction source {source} to {destination} …")
                if is_directory:
                    container.cp_dir_from(str(source), destination)
                else:
                    container.cp_from(str(source), destination)

    if include_shared:
        raf_archive = PRIOR_WORK / "RAF-AG/Dataset.zip"
        with zipfile.ZipFile(raf_archive) as archive:
            archive.extractall(raf_archive.parent)

        ladder_archive = RESOURCES / "LADDER_table_9_data.zip"
        with zipfile.ZipFile(ladder_archive) as archive:
            archive.extractall(PRIOR_WORK / "LADDER")

    buchel_bosch = buchel_ext_tools_root / "dataset/bosch_test.json"
    if not buchel_bosch.is_file():
        raise FileNotFoundError(
            f"Buchel ext_tools dataset is missing: {buchel_bosch}. "
            "Pass the extracted ext_tools directory with --buchel-ext-tools-root."
        )
    buchel_destination = PRIOR_WORK / "Buchel/bosch_test.json"
    buchel_destination.parent.mkdir(parents=True, exist_ok=True)
    buchel_destination.write_bytes(buchel_bosch.read_bytes())

    if include_shared:
        unfetter_destination = PRIOR_WORK / "rcATT/unfetter.csv"
        unfetter_destination.parent.mkdir(parents=True, exist_ok=True)
        unfetter_env = os.environ.copy()
        unfetter_env["PYTHONPATH"] = os.pathsep.join(
            part for part in (str(PROJECT_ROOT), unfetter_env.get("PYTHONPATH")) if part
        )
        subprocess.run(
            [sys.executable, str(RESOURCES / "get_unfetter_data.py"),
             "--out", str(unfetter_destination)],
            cwd=str(BASE_DIR),
            env=unfetter_env,
            check=True,
        )
    unfetter_dictionary = PRIOR_WORK / "rcATT/dict_wiki"
    actual_unfetter_sha256 = hashlib.sha256(unfetter_dictionary.read_bytes()).hexdigest()
    if actual_unfetter_sha256 != UNFETTER_DICT_SHA256:
        raise RuntimeError(
            f"Unfetter dict_wiki checksum mismatch: expected {UNFETTER_DICT_SHA256}, "
            f"found {actual_unfetter_sha256}"
        )
    print(f"Reproduction sources are ready under {PRIOR_WORK}")


parser = argparse.ArgumentParser(
    description="Compile benchmark data and collect inputs for Tests/Reproductions"
)
parser.add_argument(
    "--reproduction-sources-only",
    action="store_true",
    help="Collect only the raw reproduction inputs; skip benchmark compilation",
)
parser.add_argument(
    "--skip-existing",
    action="store_true",
    help="Exit successfully without container access when all concrete outputs already exist",
)
parser.add_argument(
    "--existing-only",
    action="store_true",
    help="Require a complete existing output; never access containers or rebuild data",
)
parser.add_argument(
    "--buchel-ext-tools-root",
    type=Path,
    default=Path(os.environ.get(
        "BUCHEL_EXT_TOOLS_ROOT",
        PROJECT_ROOT / "Docker_Setup/Buchel/buchel_generation/ext_tools",
    )),
    help="Path to the extracted Buchel ext_tools directory",
)
collection_args = parser.parse_args()
output_issues = compilation_output_issues()
if collection_args.existing_only:
    if output_issues:
        raise SystemExit(
            "Compiled datasets are incomplete. A first-time compilation requires all "
            "source containers; run install.sh --all.\n  " + "\n  ".join(output_issues)
        )
    print("Compiled benchmark and prior-work data already exist; skipping compilation.")
    raise SystemExit(0)
if collection_args.skip_existing and not output_issues:
    print("Compiled benchmark and prior-work data already exist; skipping compilation.")
    raise SystemExit(0)
if collection_args.reproduction_sources_only:
    collect_reproduction_sources(collection_args.buchel_ext_tools_root.resolve())
    raise SystemExit(0)


#############################################################
#                                                           #
#                   compile TTPDrill data                   #
#                                                           #
#############################################################
# TTPDrill data is in the version 0.5 repository that we did not include in the docker build (doesn't run)
# so here we just pull it from github
# This sentence data
print("Downloading TTPDrill data …")
data_link = "https://github.com/KaiLiu-Leo/TTPDrill-0.5/raw/master/ontology/examples/All.csv"
filepath = PRIOR_WORK / "TTPDrill/All.csv"
filepath.parent.mkdir(parents=True, exist_ok=True)
download_file(data_link, filepath)    

print(f"Skipping TTPDrill sentences. Uncomment to process ...")
"""
print("Loading TTPDrill data …")
ttpdrill = pd.read_csv(
    filepath,
    usecols=["text", "id"],
    encoding="latin1",
    engine="python",
    quoting=csv.QUOTE_MINIMAL,
    dtype=str
)
print("Processing TTPDrill data …")
for _, r in tqdm.tqdm(ttpdrill.iterrows(), total=ttpdrill.shape[0], desc="TTPDrill"):
    
    ids_raw = r["id"].strip().upper()
    single  = ids_raw if ids_raw and ids_raw.lower() != "nan" else None
    text = r["text"]

    report = {
        "text": text,
        "sha1_hash": compute_sha1(text),
        "title": None,          
        "filepath": str(filepath),          
        "provenance": "TTPDrill",    
        "source": "ATT&CK",     
        "purpose": "unk",            
        "ground_truth": [single],
        "attack_version": "unk",
        "modality":"sentence"
    }

    save_report_with_dedup(report)"""


#############################################################
#                                                           #
#                   compile TRAM data                       #
#                                                           #
#############################################################
# copy the TRAM data from the container

src_path = Path(adapter_workspace_map.get("TRAM")) / "data/training/tram2_data/mjson_files"
dest_path = PRIOR_WORK / "TRAM/mjson_files"
dest_path.mkdir(parents=True, exist_ok=True)
print(f"Copying TRAM data from container {src_path} to {dest_path} …")
copy_from_container("TRAM", str(src_path), str(dest_path))

print("Processing TRAM mjson files …")
for p in tqdm.tqdm(dest_path.glob("*.mjson"), desc="TRAM mjson files"):
    raw = json.loads(p.read_text(encoding="utf-8")) 
    text   = raw.get("signal", "")
    asets  = raw.get("asets", [])
    
    sha1_hash = compute_sha1(text)

    labels = set()
    labeled_sents = []

    for aset in asets:
        m = TTP_CODE_PATTERN.search(aset.get("type", ""))
        if not m:
            continue
        ttp = m.group()
        labels.add(ttp)

        # we don't need sentence-level predicton on TRAM reports
        """for start, stop, *_ in aset.get("annots", []):
            sent = text[start:stop].strip()
            if sent:
                labeled_sents.append({
                    "ground_truth": ttp,
                    "sentence_text":sent
                })"""
    
    report = {
        "text": text,
        "sha1_hash": sha1_hash,
        "title": p.stem,          
        "filepath": str(p),          
        "provenance": "TRAM",   
        "source": "manual",      
        "purpose": "training",            
        "ground_truth": list(labels),
        "attack_version": "13.1",
        "modality":"report"
    }

    save_report_with_dedup(report)

print("Skipping TRAM training sentence data. Uncomment to process ...")
"""file_paths = ["data/training/tram2_data/multi_label.json", "data/training/tram2_data/single_label.json"]
for filepath in file_paths:
    # copy the files from the container
    print(f"Copying TRAM {filepath} from container …")
    src_path = Path(adapter_workspace_map.get("TRAM")) / filepath
    dest_path = PRIOR_WORK / "TRAM" / Path(filepath).name
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    copy_from_container("TRAM", str(src_path), str(dest_path))
    print(f"Processing TRAM {filepath} …")
    for itm in tqdm.tqdm(load_json(dest_path), desc=f"TRAM {filepath}"):
        if itm.get('sentence', None):
            text = itm['sentence']
        else:
            text = itm['text']
        if itm.get("labels", None) is not None:
            ground_truth = list(itm.get("labels", []))
        if itm.get("label", None):
            ground_truth = [itm.get("label", '')]
        report = {
            "text": text,
            "sha1_hash": compute_sha1(text),
            "title": itm.get("doc_title"),          
            "filepath": str(filepath),          
            "provenance": "TRAM",      
            "source": "manual",  
            "purpose": "training",            
            "ground_truth": ground_truth,
            "attack_version": "13.1",
            "modality": "sentence"
        }

        save_report_with_dedup(report)

file_paths = ["data/training/attack_may_2023_merged_bootstrap_data2.json", "data/training/bootstrap-training-data.json"]

# Note that the TRAM bootsrap data might be natural languge from ATT&CK website related to techniuqes
# Others do something similar so this data may need better de-duplication if used in conjunction with other data
for filepath in file_paths:
    # copy the files from the container
    print(f"Copying TRAM {filepath} from container …")
    src_path = Path(adapter_workspace_map.get("TRAM")) / filepath
    dest_path = PRIOR_WORK / "TRAM" / Path(filepath).name
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    copy_from_container("TRAM", str(src_path), str(dest_path))
    
    print(f"Processing TRAM {filepath} …")
    data = load_json(dest_path)
    for itm in tqdm.tqdm(data["sentences"], desc=f"TRAM {filepath}"):
        if itm["disposition"] == "accept":
            labels = [i["attack_id"] for i in itm["mappings"]]
            text = itm["text"]
            report = {
                "text": text,
                "sha1_hash": compute_sha1(text),
                "title": None,          
                "filepath": str(filepath),
                "provenance": "TRAM",         
                "source": "manual",  
                "purpose": "training",            
                "ground_truth": [itm.get("label", '')],
                "attack_version": "13.1",
                "modality": "sentence"
            }

            save_report_with_dedup(report)"""

#############################################################
#                                                           #
#                   compile RAF-AG data                     #
#                                                           #
#############################################################

RAF_AG_DATASET =  PRIOR_WORK / "RAF-AG" / "Dataset"
RAF_AG_REPORTS      =  RAF_AG_DATASET / "CTI reports"
GROUND_TRUTH    =  RESOURCES / "raf_ag_ground_truth_labels.json"

print("Copying RAF-AG data from container …")
# copy from container
src_path = Path(adapter_workspace_map.get("RAF-AG")) / "Dataset.zip"
dest_path = PRIOR_WORK / "RAF-AG" / "Dataset.zip"
dest_path.parent.mkdir(parents=True, exist_ok=True)
copy_from_container("RAF-AG", str(src_path), str(dest_path))

print(f"Extracting RAF-AG {dest_path} …")
with zipfile.ZipFile(dest_path) as zf:
    zf.extractall(dest_path.parent)          

gt_dict = load_json(GROUND_TRUTH)

print(f"Processing RAF-AG reports {len(list(RAF_AG_REPORTS.glob('*.txt')))} …")
for p in tqdm.tqdm(RAF_AG_REPORTS.glob("*.txt"), total=len(list(RAF_AG_REPORTS.glob("*.txt"))), desc="RAF-AG reports"):
    #match title to GROUND_TRUTH json
    found = False
    for gt in gt_dict:
        file_name = gt.get("txt file", "")
        if p.name.lower() == file_name.lower():
            labels = gt.get("labels", [])
            found = True
            break
    if not found:
        raise ValueError(f"Could not find ground truth labels for RAF-AG report {p.name.lower()}")

    text = p.read_text(encoding="utf-8", errors="ignore")

    sha1_hash = compute_sha1(text)
    
    report = {
        "text": text,
        "sha1_hash": sha1_hash,
        "title": p.stem,          
        "filepath": str(p),          
        "provenance": "RAF-AG",         
        "source": "manual",  
        "purpose": "testing",            
        "ground_truth": list(set(labels)),
        "attack_version": "unk",
        "modality":"report"
    }

    save_report_with_dedup(report)

#############################################################
#                                                           #
#                   compile AttacKG data                    #
#                                                           #
#############################################################

print("Copying AttacKG data from container …")
src_path = "Dataset/Evaluation"
dest_path = PRIOR_WORK / "AttacKG"
dest_path.mkdir(parents=True, exist_ok=True)
copy_from_container("AttacKG", src_path, str(dest_path))
attackg_file_map = load_json(RESOURCES / "attackg_ground_truth_and_test_labels.json")
print("Processing AttacKG reports …")
# Note predictions are original predictions and labels are ground truth
for file in attackg_file_map:
    if file.get("labels") == []:
        continue  # skip the 8 files that do not have ground truth
    fname = file.get("file name", "")
    title = file.get("title")
    labels = file.get("labels", [])
    report_path = Path(dest_path / fname)
    text_content = report_path.read_text(encoding="utf-8").strip()
    sha1_hash = compute_sha1(text_content)
    report = {
        "title": title,
        "sha1_hash": sha1_hash,
        "text": text_content,
        "ground_truth": labels,
        "filepath": [str(report_path), str(GROUND_TRUTH)],  
        "provenance":"AttacKG",
        "source": "manual",
        "purpose":"testing",
        "attack_version":"unk",
        "modality":"report"
    }

    save_report_with_dedup(report)


#############################################################
#                                                           #
#                    compile rcATT data                     #
#                                                           #
#############################################################

print("Copying rcATT data from container …")
src_path = Path(adapter_workspace_map.get("rcATT")) / "classification_tools/data/training_data_original.csv"
dest_path = PRIOR_WORK / "rcATT/training_data_original.csv"
dest_path.parent.mkdir(parents=True, exist_ok=True)
copy_from_container("rcATT", str(src_path), str(dest_path))

print("Skipping rcATT training sentence data. Uncomment to process ...")
"""print("Processing rcATT training data …")
csv.field_size_limit(sys.maxsize)
rcaTT = pd.read_csv(
    dest_path,
    encoding="latin1",
    engine="python",
    quoting=csv.QUOTE_MINIMAL,
    dtype=str
)

for _, r in tqdm.tqdm(rcaTT.iterrows(), desc="rcATT"):
    labels = []
    for col in rcaTT.columns:
        if col != "Text":
            if r[col] == "1" or r[col] == 1:
                labels.append(col)
    text = r['Text']
    report = {
        "text": text,
        "sha1_hash": compute_sha1(text),
        "title": None,          
        "filepath": str(filepath),          
        "provenance": "rcATT",         
        "source": "ATT&CK",
        "purpose": "training",            
        "ground_truth": labels,
        "attack_version": "unk",
        "modality":"sentence"
    }

    save_report_with_dedup(report)"""

#############################################################
#            Also UNFETTER DATA TO TEST RCATT               #
#############################################################
print("Downloading Unfetter data …")
unfetter_dest = PRIOR_WORK / "rcATT/unfetter.csv"
unfetter_dest.parent.mkdir(parents=True, exist_ok=True)
unfetter_script_path = BASE_DIR / "resources/get_unfetter_data.py"
unfetter_env = os.environ.copy()
unfetter_env["PYTHONPATH"] = os.pathsep.join(
    part for part in (str(PROJECT_ROOT), unfetter_env.get("PYTHONPATH")) if part
)
subprocess.run(
    [sys.executable, str(unfetter_script_path), "--out", str(unfetter_dest)],
    cwd=str(BASE_DIR),
    env=unfetter_env,
    check=True,
)

print("Processing Unfetter data …")
unfetter = pd.read_csv(
    unfetter_dest,
    encoding="latin1",
    engine="python",
    quoting=csv.QUOTE_MINIMAL,
    dtype=str
)

for i, r in tqdm.tqdm(unfetter.iterrows(), desc="Unfetter for rcATT tests", total=unfetter.shape[0]):
    labels = []
    for col in unfetter.columns:
        if col != "Text":
            if r[col] == "1" or r[col] == 1:
                labels.append(col)

    text = r["Text"]
    sha1 = compute_sha1(text)
    main_path = REPORTS_DIR / f"{sha1}.json"

    if "PHP version of the China" in text:
        print("!!!!! FOUND IT !!!!!")
        print("row:", i)
        print("sha1:", sha1)
        print("exists before save:", main_path.exists(), main_path)

        if main_path.exists():
            existing = json.loads(main_path.read_text(encoding="utf-8"))
            print("existing provenance:", existing.get("provenance"))
            print("existing filepath:", existing.get("filepath"))
            print("same text:", existing.get("text") == text)
            print("existing labels:", existing.get("ground_truth"))
            print("new labels:", labels)

    report = {
        "text": text,
        "sha1_hash": sha1,
        "title": None,
        "filepath": str(unfetter_dest),
        "provenance": "rcATT",
        "purpose": "training",
        "source": "other",
        "ground_truth": labels,
        "attack_version": "unk",
        "modality":"report"
    }

    save_report_with_dedup(report)

    if "PHP version of the China" in text: #
        print("exists after save:", main_path.exists(), main_path)
        text = " THIS FILE MIGHT BE GETTING DELETED BY ANTI-VIRUS"
        report = {
            "text": text,
            "sha1_hash": "c03bbf0fb672d364eba8dece1bb32c4f1d79e14a-duplicate",
            "title": None,
            "filepath": str(unfetter_dest),
            "provenance": "rcATT",
            "purpose": "training",
            "source": "other",
            "ground_truth": labels,
            "attack_version": "unk",
            "modality":"report"
        }

        save_report_with_dedup(report)


#############################################################
#                                                           #
#                 compile Orbinato data                     #
#                                                           #
#############################################################
# sentence data
print("Copying Orbinato sentence data from container …")
src_path = Path(adapter_workspace_map.get("Orbinato")) / "data/dataset.csv"
dest_path = PRIOR_WORK / "Orbinato/dataset.csv"
dest_path.parent.mkdir(parents=True, exist_ok=True)
copy_from_container("Orbinato", str(src_path), str(dest_path))

print("Skipping Orbinato sentence data. Uncomment to process ...")
"""print("Processing Orbinato sentence data …")
orbinato =  pd.read_csv(
    dest_path,
    encoding="latin1",
    engine="python",
    quoting=csv.QUOTE_MINIMAL,
    dtype=str
)

for _, r in tqdm.tqdm(orbinato.iterrows(), desc="Orbinato sentence data", total=orbinato.shape[0]):
    text = r["sentence"]
    labels = list(set([r["label_tec"], r["label_subtec"]]))
    report = {
        "text": text,
        "sha1_hash": compute_sha1(text),
        "title": None,          
        "filepath": str(filepath),          
        "provenance": "Orbinato",      
        "source": "manual",  
        "purpose": "training",              # each classifier splits the dataset differently during training process, so we treat it as all training
                                            # refer to the train process to determine appropriate splitting to reproduce results
        "ground_truth": labels,
        "attack_version": "10.1",
        "modality":"sentence"
    }

    save_report_with_dedup(report)"""

print("Copying Orbinato APT documents from container …")
src_path = Path(adapter_workspace_map.get("Orbinato")) / "src/apt_documents"
dest_path = PRIOR_WORK / "Orbinato/apt_documents"
dest_path.parent.mkdir(parents=True, exist_ok=True)
copy_from_container("Orbinato", str(src_path), str(dest_path))

print("Processing Orbinato APT documents …")
# load ground truth json
ground_truth_json = RESOURCES / "orbinato_ground_truth_consolidated.json"
ground_truth = load_json(ground_truth_json)
"""
[   
    {
        "paper_ref": "FIN6 [20]",
        "file_path": "FIN6/Follow The Money-Dissecting the Operations of the Cyber Crime Group FIN6[1].txt",
        "ground_truth": ["T1087", "T1560", "T1119", "T1547", "T1110", "T1059", "T1074", "T1573", "T1068", "T1070", 
        "T1046", "T1003", "T1572", "T1021", "T1018", "T1053", "T1078", "T1003"]
    },
    ...
]
"""
for item in ground_truth:
    file_path = dest_path / item["file_path"]
    text = file_path.read_text(encoding="utf-8", errors="ignore")
    labels = item.get("ground_truth", [])
    report = {
        "text": text,
        "sha1_hash": compute_sha1(text),
        "title": None,          
        "filepath": str(file_path),          
        "provenance": "Orbinato",     
        "source": "manual",  
        "purpose": "testing", 
        "ground_truth": labels,
        "attack_version": "10.1",
        "modality":"report"
    }

    save_report_with_dedup(report)

#############################################################
#                                                           #
#                  compile TTP-LLM data                     #
#                                                           #
#############################################################
# sentence data
print("Copying TTP-LLM sentence data from container …")
src_path = Path(adapter_workspace_map.get("TTP-LLM")) / "data/MITRE_Procedures.csv"
dest_path = PRIOR_WORK / "TTP-LLM/MITRE_Procedures.csv"
dest_path.parent.mkdir(parents=True, exist_ok=True)
copy_from_container("TTP-LLM", str(src_path), str(dest_path))

print("Skipping TTP-LLM sentence data. Uncomment to process ...")
"""print("Processing TTP-LLM sentence data …")
ttp_llm =  pd.read_csv(
    dest_path,
    encoding="latin1",
    engine="python",
    quoting=csv.QUOTE_MINIMAL,
    dtype=str
)

_TACTIC_ID = {
    "INITIAL_ACCESS": "TA0001",
    "EXECUTION": "TA0002",
    "PERSISTENCE": "TA0003",
    "PRIVILEGE_ESCALATION": "TA0004",
    "DEFENSE_EVASION": "TA0005",
    "CREDENTIAL_ACCESS": "TA0006",
    "DISCOVERY": "TA0007",
    "LATERAL_MOVEMENT": "TA0008",
    "COLLECTION": "TA0009",
    "COMMAND_AND_CONTROL": "TA0010",
    "EXFILTRATION": "TA0011",
    "IMPACT": "TA0040",
    "RECONNAISSANCE": "TA0043",
    "RESOURCE_DEVELOPMENT": "TA0042",
}

TACTIC_COLS = ["Tactic1", "Tactic2", "Tactic3", "Tactic4"]

for _, r in tqdm.tqdm(ttp_llm.iterrows(), total=ttp_llm.shape[0], desc="TTP-LLM"):
    originals = []
    labels = []
    for col in TACTIC_COLS:
        name = r[col]
        if pd.isna(name) or not name:
            continue
        else:
            labels.append(_TACTIC_ID[name])
            originals.append(name)
    text = r['Procedures']
    report = {
        "text": text,
        "sha1_hash": compute_sha1(text),
        "title": None,          
        "filepath": str(dest_path),          
        "provenance": "TTP_LLM",         
        "purpose": "testing", 
        "source": "manual",
        "ground_truth": labels,
        "attack_version": "unk",
        "modality":"sentence"
    }

    save_report_with_dedup(report)"""

#############################################################
#                                                           #
#                  compile AnnoCTR data                     #
#                                                           #
#############################################################
# use Buchel's version of the AnnoCTR data

print("Processing AnnoCTR data from Buchel container …")
datasets = ["bosch_cti_test_ds.json", "bosch_cti_devtrain_ds.json"]

for dataset in datasets:
    print(f"Copying AnnoCTR dataset {dataset} …")
    src_path = Path(adapter_workspace_map.get("Buchel")) / f"finetuning/cti_datasets/bosch/{dataset}"
    dest_path = PRIOR_WORK / "AnnoCTR" / dataset
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    copy_from_container("Buchel", str(src_path), str(dest_path))
    print(f"Processing AnnoCTR dataset {dataset} …")
    df = pd.read_json(dest_path, encoding="utf-8")

    # ensure each cell in 'mitre_ids' is a list
    df['mitre_ids'] = df['mitre_ids'].apply(
        lambda v: v if isinstance(v, list) else ([] if pd.isna(v) else [v])
    )

    test_docs = (
        df.groupby('cti_report', as_index=False)
          .agg(ground_truth=('mitre_ids',
                                lambda s: sorted(set(chain.from_iterable(s)))))
          .rename(columns={'cti_report': 'text'})
    )
    # update ground_truth to only TTP codes
    test_docs['ground_truth'] = test_docs['ground_truth'].apply(
        lambda labels: [label for label in labels if label.startswith("T")]
    )
    # remove test docs with empty ground_truth
    test_docs = test_docs[test_docs['ground_truth'].map(len) > 0]

    for _, row in tqdm.tqdm(test_docs.iterrows(), total=test_docs.shape[0], desc=f"AnnoCTR {dataset}"):
    # check for MITRE TTPs in the mitre_ids list 
    
        report = {
            "text": row['text'],
            "sha1_hash": compute_sha1(row['text']),
            "title": None,
            "filepath": str(dest_path),
            "provenance": "Buchel",
            "source": "manual", 
            "purpose": "testing" if "test" in dataset else "training",
            "ground_truth": row['ground_truth'],
            "attack_version": "unk",
            "modality": "report",
        }
        save_report_with_dedup(report)


#############################################################
#                                                           #
#                  compile LADDER data                      #
#                                                           #
#############################################################

# LADDER data is in the resources/LADDER_table_9_data as seperate text and json files with ground truth
print("Processing LADDER data …")
LADDER_DATASET = RESOURCES / "LADDER_table_9_data.zip"
dest_path = PRIOR_WORK / "LADDER" / dataset
# unzip LADDER data
with zipfile.ZipFile(LADDER_DATASET, 'r') as zip_ref:
    zip_ref.extractall(PRIOR_WORK / "LADDER")
LADDER_DATASET = PRIOR_WORK / "LADDER" / "LADDER_table_9_data"
for text_file in LADDER_DATASET.glob("*.txt"):
    json_file = LADDER_DATASET / f"{text_file.stem}.json"
    if not json_file.exists():
        print(f"Warning: no matching JSON file for {text_file}, skipping …")
        continue
    text = text_file.read_text(encoding="utf-8", errors="ignore")
    raw_labels = load_json(json_file)
    labels = []
    for ttp in raw_labels.get('ground_truth_ttps', []):
        labels.append(ttp)
    report = {
        "text": text,
        "sha1_hash": compute_sha1(text),
        "title": None,          
        "filepath": (str(text_file), str(json_file)),          
        "provenance": "LADDER",         
        "source": "manual",  
        "purpose": "testing",            
        "ground_truth": labels,
        "attack_version": "unk",
        "modality":"report"
    }

    save_report_with_dedup(report)

#############################################################
#                                                           #
#                  compile SeqMask data                     #
#                                                           #
#############################################################
# Pull the seqmask data from the container
print("Copying SeqMask data from container …")
src_path = Path(adapter_workspace_map.get("SeqMask")) / "datas"
dest_path = PRIOR_WORK / "SeqMask"
dest_path.parent.mkdir(parents=True, exist_ok=True)
copy_from_container("SeqMask", str(src_path), str(dest_path))

print("Skipping SeqMask data. Uncomment to process ...")
"""print("Processing SeqMask data …")
datas = ["data_origin4.csv", "data_origin13.csv", "TTPDrill-subTTP.csv"]
for data in datas:
    data_path = dest_path / data
    seqmask = pd.read_csv(
        data_path,
        encoding="latin1",
        engine="python",
        quoting=csv.QUOTE_MINIMAL,
        dtype=str
    )
    if data == "data_origin4.csv":
        # unzip this one first
        print("Unzipping SeqMask data_origin4.zip …")
        with zipfile.ZipFile(dest_path / "data_origin4.zip", 'r') as zip_ref:
            zip_ref.extractall(dest_path)
    print(f"Processing SeqMask {data} …")
    for _, r in tqdm.tqdm(seqmask.iterrows(), total=seqmask.shape[0], desc=f"SeqMask {data}"):
        if data == "data_origin4.csv" or data == "data_origin13.zip":
            # text is in 'Text', TTPs are one-hot-encoded 
            text = r["Text"]
            labels = []
            for col in seqmask.columns:
                if col != "Text" or col != "processed":
                    if r[col] == "1" or r[col] == 1:
                        labels.append(col)
        if data == "TTPDrill-subTTP.csv":
            # text is in 'text', TTPs are one-hot-encoded 
            text = r["text"]
            labels = [r["id"]]
        report = {
            "text": text,
            "sha1_hash": compute_sha1(text),
            "title": None,          
            "filepath": str(data_path),          
            "provenance": "SeqMask",    
            "source": "ATT&CK",      # all three sources
            "purpose": "unk",            
            "ground_truth": labels,
            "attack_version": "unk",
            "modality":"sentence",
        }

        save_report_with_dedup(report)"""

#############################################################
#                                                           #
#            compile author labeled reports                 #
#                                                           #
#############################################################

print("Unzipping author labeled reports data to author_labeled_reviewed …")
zip_path = RESOURCES / "author_labeled_reviewed.zip"
dest = Path("author_labeled_reviewed")
dest.mkdir(parents=True, exist_ok=True)

with zipfile.ZipFile(zip_path, "r") as zf:
    names = [n for n in zf.namelist() if not n.endswith("/")]
    has_prefix = any(n.startswith("author_labeled_reviewed/") for n in names)

    if has_prefix:
        zf.extractall(dest.parent)
    else:
        zf.extractall(dest)

# Collect only the additional files that the reproduction data layout needs.
# Sources shared with the benchmark compilation above are reused in place.
collect_reproduction_sources(
    collection_args.buchel_ext_tools_root.resolve(),
    include_shared=False,
)
