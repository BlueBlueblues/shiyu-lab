"""Verify archived application files against the confirmed production snapshot."""
import hashlib, json
from pathlib import Path
root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / "docs/SOURCE_MANIFEST.json").read_text())
errors = [name for name, digest in manifest["sha256"].items()
          if not (root/name).is_file() or hashlib.sha256((root/name).read_bytes()).hexdigest() != digest]
if errors:
    raise SystemExit("Source mismatch: " + ", ".join(errors))
print(f"Verified {len(manifest['sha256'])} production application/dependency files")
