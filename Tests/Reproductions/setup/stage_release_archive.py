#!/usr/bin/env python3
"""Validate and stage the monolithic release models.zip without a payload manifest."""
from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import stat
import zipfile
import zlib


COMPONENTS = {
    "buchel/sft_bosch.zip",
    "buchel/generation.zip",
    "buchel/ext_tools.zip",
    "buchel/LLAMA_3.1_LICENSE.txt",
    "buchel/NOTICE",
    "orbinato/models.zip",
    "orbinato/additional_files.zip",
    "orbinato/LICENSE.txt",
    "orbinato/SECBERT_LICENSE.txt",
    "orbinato/MITRE_LICENSE.txt",
    "seqmask/models.zip",
    "seqmask/LICENSE.txt",
    "benchmark/results.zip",
}
TRAM_PREFIX = PurePosixPath("buchel/sft_tram/merged")
TRAM_FILES = {
    "config.json",
    "generation_config.json",
    "model-00001-of-00007.safetensors",
    "model-00002-of-00007.safetensors",
    "model-00003-of-00007.safetensors",
    "model-00004-of-00007.safetensors",
    "model-00005-of-00007.safetensors",
    "model-00006-of-00007.safetensors",
    "model-00007-of-00007.safetensors",
    "model.safetensors.index.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
}


def safe_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "" in path.parts:
        raise ValueError(f"Unsafe models.zip member: {name!r}")
    return path


def existing_crc(path: Path, chunk_size: int = 8 * 1024 * 1024) -> int:
    checksum = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            checksum = zlib.crc32(chunk, checksum)
    return checksum & 0xFFFFFFFF


def extract_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path) -> None:
    if target.is_file() and target.stat().st_size == info.file_size and existing_crc(target) == info.CRC:
        return
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Refusing to replace changed staged asset: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    partial.unlink(missing_ok=True)
    checksum = 0
    size = 0
    try:
        with archive.open(info) as source, partial.open("wb") as destination:
            while chunk := source.read(8 * 1024 * 1024):
                destination.write(chunk)
                checksum = zlib.crc32(chunk, checksum)
                size += len(chunk)
        if size != info.file_size or (checksum & 0xFFFFFFFF) != info.CRC:
            raise ValueError(f"Extracted asset failed ZIP integrity validation: {info.filename}")
        partial.chmod(0o640)
        os.replace(partial, target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--external-root", type=Path, required=True)
    args = parser.parse_args()

    archive_path = args.archive.resolve()
    if not archive_path.is_file():
        raise FileNotFoundError(f"models.zip not found: {archive_path}")

    with zipfile.ZipFile(archive_path) as archive:
        files: dict[str, zipfile.ZipInfo] = {}
        for info in archive.infolist():
            path = safe_path(info.filename.rstrip("/"))
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise ValueError(f"Archive symlinks are not accepted: {info.filename}")
            if info.is_dir():
                continue
            if info.filename in files:
                raise ValueError(f"Duplicate models.zip member: {info.filename}")
            files[info.filename] = info

        actual_components = COMPONENTS & files.keys()
        missing_components = COMPONENTS - actual_components
        tram = {
            path.name: info
            for name, info in files.items()
            if (path := safe_path(name)).parent == TRAM_PREFIX
        }
        unexpected = set(files) - COMPONENTS - {
            str(TRAM_PREFIX / name) for name in TRAM_FILES
        }
        if missing_components or set(tram) != TRAM_FILES or unexpected:
            raise ValueError(
                "models.zip inventory mismatch; "
                f"missing_components={sorted(missing_components)}, "
                f"missing_tram={sorted(TRAM_FILES - set(tram))}, "
                f"extra={sorted(unexpected | {str(TRAM_PREFIX / name) for name in set(tram) - TRAM_FILES})}"
            )

        output = args.output_root.resolve()
        external = args.external_root.resolve()
        for name in sorted(COMPONENTS):
            extract_member(archive, files[name], output / Path(name))
        tram_output = external / "Buchel/models/zenodo/mitre_sentence_tram/merged"
        for name in sorted(TRAM_FILES):
            extract_member(archive, tram[name], tram_output / name)

    print("Validated and staged models.zip inventory and ZIP integrity")
    print(f"Component archives: {args.output_root.resolve()}")
    print(f"Published TRAM checkpoint: {tram_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
