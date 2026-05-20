from pathlib import Path
import re

files = [
    Path(r"apps/web/app/evidence-audit-panel.tsx"),
    Path(r"services/api/tests/test_proof_bundle_export.py"),
]

pattern = re.compile(
    r"<<<<<<<[^\n]*\n(.*?)\n=======\n(.*?)\n>>>>>>>[^\n]*",
    re.DOTALL,
)

for p in files:
    s = p.read_text(encoding="utf-8-sig")
    total = 0

    while True:
        s, n = pattern.subn(lambda m: m.group(2), s)
        total += n
        if n == 0:
            break

    p.write_text(s, encoding="utf-8")
    print(f"{p}: resolved {total} conflict blocks")