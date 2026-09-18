#!/usr/bin/env python3
"""Fetch the single published TRAM model used by the Table 9 reproduction."""
import concurrent.futures
import io
import os
from pathlib import Path
import struct
import urllib.request
import zipfile
import zlib

URL = "https://zenodo.org/records/16753555/files/Repo%20Models.zip?download=1"
PREFIX = "Repo Models/gLLM/SFT Tram LLama3.1 8b/"
SETUP = Path(__file__).resolve().parent
SUITE = SETUP.parent
TOOLS = Path(os.environ.get("REPRO_EXTERNAL_ROOT", SUITE / ".runtime/external_tools")).resolve()
DEST = TOOLS / "Buchel/models/zenodo/mitre_sentence_tram/merged"
TOKENIZER_SOURCE = TOOLS / "Buchel/models/local/bosch_sentence/merged"
TOKENIZER_FILES = ("tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "added_tokens.json")
PUBLISHED_FILES = {
    "config.json", "generation_config.json", "model.safetensors.index.json",
    *(f"model-{number:05d}-of-00007.safetensors" for number in range(1, 8)),
}

def seed_tokenizer():
    for name in TOKENIZER_FILES:
        source = TOKENIZER_SOURCE / name
        target = DEST / name
        if source.is_file() and not target.exists():
            target.write_bytes(source.read_bytes())
            print("Seeded tokenizer:", target.name, flush=True)

def staged_checkpoint_complete():
    return all((DEST / name).is_file() and (DEST / name).stat().st_size > 0 for name in PUBLISHED_FILES)

def response(start, size):
    request = urllib.request.Request(URL, headers={"Range": "bytes={}-{}".format(start, start + size - 1)})
    r = urllib.request.urlopen(request, timeout=120)
    if r.status != 206:
        r.close()
        raise RuntimeError("Zenodo did not honor Range")
    return r

class Remote(io.RawIOBase):
    def __init__(self, size): self.size, self.pos = size, 0
    def seekable(self): return True
    def tell(self): return self.pos
    def seek(self, offset, whence=0):
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.size + offset
        return self.pos
    def read(self, n=-1):
        n = self.size - self.pos if n < 0 else min(n, self.size - self.pos)
        if n <= 0: return b""
        if n > 4_000_000: raise RuntimeError("Unexpected ZIP metadata size")
        with response(self.pos, n) as r: data = r.read(n + 1)
        if len(data) != n: raise RuntimeError("Truncated metadata")
        self.pos += n
        return data

def download(entry):
    target = DEST / Path(entry.filename).name
    if target.exists():
        if target.stat().st_size != entry.file_size: raise RuntimeError("Existing size mismatch: " + str(target))
        print("Already present:", target.name, flush=True)
        return
    with response(entry.header_offset, 30) as r: header = r.read()
    name_len, extra_len = struct.unpack_from("<HH", header, 26)
    start = entry.header_offset + 30 + name_len + extra_len
    decoder = zlib.decompressobj(-15) if entry.compress_type == zipfile.ZIP_DEFLATED else None
    if entry.compress_type not in (zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED): raise RuntimeError("Unsupported compression")
    partial = target.with_suffix(target.suffix + ".part")
    crc, written = 0, 0
    print("Downloading:", target.name, flush=True)
    with response(start, entry.compress_size) as r, partial.open("wb") as out:
        remaining = entry.compress_size
        while remaining:
            chunk = r.read(min(8 * 1024 * 1024, remaining))
            if not chunk: raise RuntimeError("Interrupted download: " + target.name)
            remaining -= len(chunk)
            data = decoder.decompress(chunk) if decoder else chunk
            out.write(data); crc = zlib.crc32(data, crc); written += len(data)
        if decoder:
            data = decoder.flush(); out.write(data); crc = zlib.crc32(data, crc); written += len(data)
    if written != entry.file_size or crc != entry.CRC: raise RuntimeError("ZIP integrity failure: " + target.name)
    partial.rename(target)
    print("Completed:", target.name, flush=True)

def main():
    DEST.mkdir(parents=True, exist_ok=True)
    seed_tokenizer()
    if staged_checkpoint_complete() and (DEST / "tokenizer.json").is_file():
        print("Using staged published TRAM checkpoint:", DEST)
        return
    # The Zenodo UI's HEAD endpoint can time out for this large file; use Range GET.
    with response(0, 1) as r:
        size = int(r.headers["Content-Range"].split("/")[-1])
        r.read()
    with zipfile.ZipFile(Remote(size)) as archive:
        entries = [i for i in archive.infolist() if i.filename.startswith(PREFIX) and not i.is_dir()]
    if not entries: raise RuntimeError("Published TRAM folder missing")
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(download, entries))
    # The published checkpoint contains the merged Llama weights but omits its
    # tokenizer. The staged AnnoCTR checkpoint uses the same Llama architecture
    # and tokenizer, so seed those small required files without retaining a
    # second TRAM checkpoint.
    seed_tokenizer()
    if not (DEST / "tokenizer.json").is_file():
        raise RuntimeError("Zenodo TRAM model needs tokenizer files from the staged AnnoCTR checkpoint")

if __name__ == "__main__": main()
