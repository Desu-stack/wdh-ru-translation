import hashlib
import os
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
if not root.is_dir():
    raise SystemExit(f"not a directory: {root}")

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

files = []
for path in root.rglob("*"):
    if not path.is_file():
        continue
    rel = path.relative_to(root).as_posix()
    if rel.startswith("_internal/") or rel == "SHA256SUMS.txt":
        continue
    files.append((rel, path))

with open(root / "SHA256SUMS.txt", "w", encoding="utf-8", newline="\n") as f:
    for rel, path in sorted(files):
        f.write(f"{sha256(path)}  {rel}\n")

print(f"wrote {root / 'SHA256SUMS.txt'} ({len(files)} files)")
