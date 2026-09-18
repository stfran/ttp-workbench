#!/usr/bin/env python3
"""Build deterministic, tool-specific private model archives."""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile

from model_archive import (
    BUCHEL_MODELS,
    MANIFEST_NAME,
    ORBINATO_FILES,
    ORBINATO_SHA256,
    REQUIRED_BUCHEL_FILES,
    SCHEMA,
    canonical_json,
    copy_and_hash,
    sha256_path,
)

ZIP_DATE = (1980, 1, 1, 0, 0, 0)


def zip_info(name: str, compression: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, ZIP_DATE)
    info.compress_type = compression
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o640) << 16
    return info


def buchel_files(root: Path):
    for model in BUCHEL_MODELS:
        merged = root / model / "merged"
        if not merged.is_dir():
            raise FileNotFoundError(f"Missing merged Buchel model directory: {merged}")
        files = {path.name: path for path in merged.iterdir() if path.is_file()}
        missing = REQUIRED_BUCHEL_FILES - set(files)
        if missing:
            raise FileNotFoundError(f"Missing files for {model}: {sorted(missing)}")
        index = json.loads(files["model.safetensors.index.json"].read_text())
        shards = set(index.get("weight_map", {}).values())
        if not shards or not all(name in files for name in shards):
            raise ValueError(f"Incomplete indexed safetensor shards for {model}")
        allowed = REQUIRED_BUCHEL_FILES | shards
        extra = set(files) - allowed
        if extra:
            raise ValueError(f"Unexpected files in {merged}: {sorted(extra)}")
        for name in sorted(allowed):
            yield f"buchel/models/local/{model}/merged/{name}", files[name], f"private Buchel {model} merged checkpoint"


def orbinato_files(source: zipfile.ZipFile):
    seen = set()
    for member in sorted(source.infolist(), key=lambda item: item.filename):
        if member.is_dir():
            continue
        parts = PurePosixPath(member.filename).parts
        if not parts or parts[0] != "models" or ".." in parts or len(parts) < 2:
            raise ValueError(f"Unexpected Orbinato model member: {member.filename}")
        if ((member.external_attr >> 16) & 0o170000) == stat.S_IFLNK:
            raise ValueError(f"Orbinato archive symlinks are not accepted: {member.filename}")
        target = "orbinato/src/" + "/".join(parts[1:])
        if target in seen:
            raise ValueError(f"Duplicate Orbinato model path: {target}")
        seen.add(target)
        yield target, member
    actual = {name.removeprefix("orbinato/src/") for name in seen}
    if actual != ORBINATO_FILES:
        raise ValueError(f"Orbinato model inventory mismatch; missing={sorted(ORBINATO_FILES - actual)}, extra={sorted(actual - ORBINATO_FILES)}")


def write_archive(out: Path, tool: str, files, source_checksums: dict, force: bool) -> None:
    if out.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite {out}; pass --force explicitly")
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.name + ".part")
    if partial.exists():
        partial.unlink()
    entries = []
    compression = zipfile.ZIP_DEFLATED if tool == "orbinato" else zipfile.ZIP_STORED
    compression_name = "deflated" if tool == "orbinato" else "stored"
    try:
        with zipfile.ZipFile(partial, "w", allowZip64=True, compresslevel=9) as destination:
            for name, source, provenance in files:
                if isinstance(source, tuple):
                    source_zip, member = source
                    stream = source_zip.open(member)
                    expected_size = member.file_size
                else:
                    if source.is_symlink():
                        raise ValueError(f"Archive inputs may not be symlinks: {source}")
                    stream = source.open("rb")
                    expected_size = source.stat().st_size
                with stream, destination.open(zip_info(name, compression), "w", force_zip64=True) as target:
                    size, digest = copy_and_hash(stream, target)
                if size != expected_size:
                    raise ValueError(f"Model member size mismatch: {name}")
                entries.append({"path": name, "size": size, "sha256": digest, "provenance": provenance})
            manifest = {
                "schema": SCHEMA,
                "archive": out.name,
                "tool": tool,
                "compression": compression_name,
                "files": sorted(entries, key=lambda entry: entry["path"]),
                "source_checksums": source_checksums,
            }
            destination.writestr(zip_info(MANIFEST_NAME, zipfile.ZIP_DEFLATED), canonical_json(manifest))
        partial.replace(out)
    except BaseException:
        if partial.exists():
            partial.unlink()
        raise

    checksum_path = out.with_suffix(".sha256")
    archive_digest = sha256_path(out)
    checksum_path.write_text(f"{archive_digest}  {out.name}\n")
    print(f"Created {out} ({out.stat().st_size} bytes)")
    print(f"SHA-256 {archive_digest}")
    print(f"Files {len(entries)}; payload bytes {sum(entry['size'] for entry in entries)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orbinato-archive", type=Path, required=True)
    parser.add_argument("--buchel-model-root", type=Path, required=True,
                        help="Directory containing bosch_sentence/")
    parser.add_argument("--buchel-out", type=Path, required=True)
    parser.add_argument("--orbinato-out", type=Path, required=True)
    parser.add_argument("--expected-orbinato-sha256", default=ORBINATO_SHA256)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    actual_orbinato = sha256_path(args.orbinato_archive)
    if actual_orbinato != args.expected_orbinato_sha256:
        raise ValueError(f"Orbinato archive SHA-256 mismatch: {actual_orbinato}")
    with zipfile.ZipFile(args.orbinato_archive) as source_zip:
        orbinato = [
            (name, (source_zip, member), "recovered Orbinato trained-model bundle")
            for name, member in orbinato_files(source_zip)
        ]
        write_archive(
            args.orbinato_out,
            "orbinato",
            orbinato,
            {"orbinato_archive_sha256": actual_orbinato},
            args.force,
        )
    write_archive(args.buchel_out, "buchel", buchel_files(args.buchel_model_root), {}, args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
