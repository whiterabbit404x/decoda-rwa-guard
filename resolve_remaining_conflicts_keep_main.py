from pathlib import Path
import re

files = [
    Path(r"apps/web/app/evidence-audit-panel.tsx"),
    Path(r"services/api/tests/test_proof_bundle_export.py"),
]

pattern = re.compile(
    r"<<<<<<<[^\r\n]*(?:\r?\n)(.*?)(?:\r?\n)=======(?:\r?\n)(.*?)(?:\r?\n)>>>>>>>[^\r\n]*(?:\r?\n|$)",
    re.DOTALL,
)

for p in files:
    s = p.read_text(encoding="utf-8-sig")
    total = 0

    while True:
        s2, n = pattern.subn(lambda m: m.group(2) + "\n", s)
        total += n
        s = s2
        if n == 0:
            break

    p.write_text(s, encoding="utf-8")
    print(f"{p}: resolved {total} remaining blocks")