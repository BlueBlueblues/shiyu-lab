"""Read-only scan of staged blobs and reachable Git history; never prints secret values.
Heuristic screening supplements manual review; it cannot prove the absence of all secrets.
"""
import json, re, subprocess
from pathlib import Path
root = Path(__file__).resolve().parents[1]
def git(*args):
    return subprocess.check_output(["git", *args], cwd=root)
secret = re.compile(rb"sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----")
forbidden = re.compile(r"(?:^|/)(?:\.env(?:\..*)?|id_rsa|id_ed25519)$|\.(?:db|sqlite3?|pem|key|zip|bak|log)$|-(?:wal|shm)$", re.I)
objects = {}
for line in git("rev-list", "--objects", "--all").decode().splitlines():
    pair = line.split(" ", 1)
    if len(pair) == 2:
        objects[(pair[0], pair[1])] = "history"
for record in git("ls-files", "--stage", "-z").decode().split("\0"):
    if record:
        meta, name = record.split("\t", 1)
        objects[(meta.split()[1], name)] = "index"
findings = []
count = 0
for (oid, name), scope in objects.items():
    if git("cat-file", "-t", oid).strip() != b"blob":
        continue
    count += 1
    data = git("cat-file", "blob", oid)
    if (forbidden.search(name) and Path(name).name != ".env.example") or data.startswith(b"SQLite format 3\0"):
        findings.append({"scope": scope, "path": name, "kind": "sensitive-file"})
    if secret.search(data):
        findings.append({"scope": scope, "path": name, "kind": "secret-pattern"})
print(json.dumps({"blobs_checked": count, "findings": findings}, ensure_ascii=False))
if findings:
    raise SystemExit(1)
