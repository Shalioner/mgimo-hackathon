from hashlib import sha256
from pathlib import Path
import sys

root = Path(sys.argv[1])
if not root.is_dir():
    sys.exit(f"Case directory is absent: {root}")
for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
    if path.is_symlink():
        sys.exit(f"Unexpected symlink: {path}")
    if path.is_file():
        print(f"{sha256(path.read_bytes()).hexdigest()}  {path.relative_to(root).as_posix()}")
