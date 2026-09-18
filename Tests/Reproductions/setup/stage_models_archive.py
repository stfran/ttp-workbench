#!/usr/bin/env python3
"""Verify and stage a private reproduction-model archive without training models."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import zipfile

from model_archive import BUCHEL_MODELS, HISTORICAL_BUCHEL_MODELS, MANIFEST_NAME, copy_and_hash, safe_member, sha256_path, validate_manifest

SETUP = Path(__file__).resolve().parent
SUITE = SETUP.parent
PROJECT = SUITE.parents[1]


def destination_paths(name: str, external: Path, orbinato_context: Path) -> list[Path]:
    path = safe_member(name)
    if path.parts[:2] == ("orbinato", "src"):
        relative = Path(*path.parts[2:])
        # Native Orbinato is later extracted from this already-modelled image;
        # staging here first would make setup_optional mistake it for a checkout.
        return [orbinato_context / relative]
    if path.parts[:3] == ("buchel", "models", "local"):
        return [external / "Buchel/models/local" / Path(*path.parts[3:])]
    raise ValueError(f"Unsupported model destination: {name}")


def same_file(path: Path, size: int, digest: str) -> bool:
    return path.is_file() and path.stat().st_size == size and sha256_path(path) == digest


def extract_member(archive: zipfile.ZipFile, name: str, target: Path, entry: dict, replace: bool) -> None:
    if same_file(target, entry["size"], entry["sha256"]):
        return
    if target.exists() and not replace:
        raise FileExistsError(f"Refusing to replace changed model file: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    if partial.exists():
        partial.unlink()
    with archive.open(name) as source, partial.open("wb") as destination:
        size, digest = copy_and_hash(source, destination)
    if size != entry["size"] or digest != entry["sha256"]:
        partial.unlink(missing_ok=True)
        raise ValueError(f"Payload checksum mismatch: {name}")
    partial.chmod(0o640)
    partial.replace(target)


def ensure_alias(root: Path, name: str) -> None:
    target = Path("secbert_model") / name
    link = root / name
    if link.is_symlink() and os.readlink(link) == str(target):
        return
    if link.exists() or link.is_symlink():
        raise FileExistsError(f"Refusing to replace Orbinato model alias: {link}")
    link.symlink_to(target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--external-root", type=Path,
                        default=Path(os.environ.get("REPRO_EXTERNAL_ROOT", SUITE / ".runtime/external_tools")))
    parser.add_argument("--orbinato-context-root", type=Path,
                        default=PROJECT / "Docker_Setup/Orbinato/private_models",
                        help="Override only for isolated archive validation tests")
    parser.add_argument("--archive-sha256", help="Optional expected checksum for the complete ZIP")
    parser.add_argument("--replace", action="store_true", help="Explicitly replace mismatching staged files")
    parser.add_argument("--buchel-model", action="append", choices=(*BUCHEL_MODELS, *HISTORICAL_BUCHEL_MODELS),
                        help="Stage only the named Büchel model; may be repeated")
    args = parser.parse_args()
    archive_digest = sha256_path(args.archive)
    if args.archive_sha256 and archive_digest != args.archive_sha256.lower():
        raise ValueError(f"Complete archive SHA-256 mismatch: {archive_digest}")
    with zipfile.ZipFile(args.archive) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos if not info.is_dir()]
        if len(names) != len(set(names)):
            raise ValueError("Archive contains duplicate members")
        for info in infos:
            if info.is_dir():
                raise ValueError(f"Directory entries are not accepted: {info.filename}")
            if ((info.external_attr >> 16) & 0o170000) == stat.S_IFLNK:
                raise ValueError(f"Archive symlinks are not accepted: {info.filename}")
        manifest = json.loads(archive.read(MANIFEST_NAME))
        entries = validate_manifest(manifest, names)
        external = args.external_root.resolve()
        selected_buchel = set(args.buchel_model or BUCHEL_MODELS)
        staged = 0
        for name, entry in sorted(entries.items()):
            path = safe_member(name)
            if (manifest["tool"] == "buchel" and path.parts[:3] == ("buchel", "models", "local")
                    and path.parts[3] not in selected_buchel):
                continue
            for target in destination_paths(name, external, args.orbinato_context_root.resolve()):
                extract_member(archive, name, target, entry, args.replace)
                staged += 1
        if manifest["tool"] == "orbinato":
            root = args.orbinato_context_root.resolve()
            ensure_alias(root, "trained_secbert.pt")
            ensure_alias(root, "secbert_labels.txt")
    print(f"Verified {len(entries)} model files and staged {staged}; archive SHA-256 {archive_digest}")
    print(f"Staged {manifest['tool']} models under {args.external_root} and the relevant build context")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
