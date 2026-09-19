# Decoda Evidence Verifier

Verify a Decoda RWA Guard evidence package **offline**, with:

* no Decoda account or login;
* no call to any Decoda API;
* no access to Decoda's database;
* no Decoda secret of any kind;
* no trust in Decoda's dashboard.

You need the evidence `.zip` and, for authenticity, Decoda's **public** verification key.

---

## Install

Nothing to install for integrity checking — Python 3.9+ standard library only.

Authenticity checking (Ed25519) needs one well-reviewed library:

```bash
pip install cryptography
```

---

## Verify

```bash
# from the repository's tools/ directory, or with tools/ on PYTHONPATH
python -m decoda_evidence_verifier verify evidence-package.zip \
    --keyring decoda-evidence-keys.json
```

No installation needed — a single invocation also works:

```bash
python tools/decoda_evidence_verifier/cli.py verify evidence-package.zip \
    --keyring decoda-evidence-keys.json
```

```text
Decoda Evidence Verifier

Package ID:         5e0f…  
Package number:     EV-2026-017
Manifest schema:    2.0
Manifest:           VALID
Files:              18/18 VALID
Manifest SHA-256:   VALID
Merkle root:        VALID
Signature:          VALID
  Algorithm:        Ed25519
  Key ID:           decoda-evidence-2026-01
  Key source:       keyring
Audit anchor:       PRESENT

Integrity:          VERIFIED
Authenticity:       VERIFIED
Audit linkage:      PRESENT
```

Machine-readable:

```bash
python -m decoda_evidence_verifier verify evidence-package.zip --json
```

Describe a package without asserting a verdict:

```bash
python -m decoda_evidence_verifier inspect evidence-package.zip
```

### Options

| Option | Meaning |
| --- | --- |
| `--keyring PATH` | A Decoda public verification keyring (JSON). |
| `--public-key VALUE` | One base64 or PEM Ed25519 **public** key. Pair with `--key-id`. |
| `--key-id ID` | The `key_id` that `--public-key` corresponds to. |
| `--allow-bundled-keyring` | Use the keyring inside the ZIP. **Off by default** — see *Trust model*. |
| `--integrity-only` | Exit `0` when integrity verifies even if authenticity cannot be established. |
| `--json` | Machine-readable output. |

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Integrity verified **and** authenticity independently verified (or integrity verified with `--integrity-only`). |
| `1` | FAILED — a hash, the manifest, the Merkle root or a signature did not verify. |
| `2` | Unusable — the package could not be read, or the command was misused. |
| `3` | Integrity verified, authenticity **not** independently verifiable (legacy HMAC-sealed package, or no usable public key supplied). |

`3` is deliberately non-zero, so a plain `if verify; then …` treats an
unverifiable-authenticity package as not-ok.

---

## The three results, and why they are separate

| Result | Values | What it means |
| --- | --- | --- |
| **Integrity** | `verified` / `failed` | Every artifact still hashes to the SHA-256 the manifest records, at the recorded size; the manifest hashes to its own `manifest_sha256`; the Merkle root recomputes. Needs **no key at all**. |
| **Authenticity** | `verified` / `unavailable` / `failed` | The manifest digest was signed by the holder of Decoda's Ed25519 private key, checked with the **public** key only. |
| **Audit linkage** | `present` / `unavailable` | The manifest records an anchor into Decoda's audit chain, covered by the manifest hash and signature. |

They are never collapsed into a single badge. A package can be perfectly intact
and still prove nothing about **who** produced it.

**Authenticity is `unavailable`, never `verified`,** when the package carries only
the legacy shared-secret HMAC seal. Verifying an HMAC requires Decoda's secret —
and anyone holding that secret could also *forge* the seal, so it can never
establish authenticity to a third party.

**Audit linkage is never reported `verified`.** Proving the chain requires the
chain, which one package does not contain.

---

## The format, in full

### Canonical JSON

```python
json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')
```

Recursive key sorting, compact separators, UTF-8, no BOM, no trailing newline.
`ensure_ascii` is **true**.

### Per-file hash

Each artifact under `artifacts/<domain>/<logical-path>` is the exact canonical-JSON
byte stream that was hashed. `sha256sum` it and compare to the manifest's `sha256`;
the byte length must equal `size_bytes`.

### Manifest hash

Remove `manifest_sha256`, re-serialize the rest as canonical JSON, SHA-256 it.
The result must equal the manifest's own `manifest_sha256`.

### Merkle root (`decoda-merkle-v1`, manifest schema 2.0+)

```text
leaf  = SHA256(0x00 || utf8(path) || 0x1F || ascii(lower(sha256_hex)))
node  = SHA256(0x01 || left_digest || right_digest)
order = leaves sorted ascending by the UTF-8 bytes of "path"
odd   = an unpaired last node is PROMOTED unchanged (never duplicated)
empty = no root at all
```

Domain separation (`0x00` / `0x01`) stops a node preimage being reinterpreted as a
leaf. Promotion rather than duplication keeps the artifact-set → root map injective
(CVE-2012-2459).

### Signature

```text
algorithm       Ed25519 (RFC 8032)
signed bytes    b"DECODA-EVIDENCE-MANIFEST-V1\x00" + bytes.fromhex(manifest_sha256)
encoding        base64 in seal.json / manifest.sig, under "signatures"
seal schema     "schema_version": 2
```

60 fixed bytes: 27-byte domain tag, `0x00`, then the raw 32-byte manifest digest.
No JSON is signed, so there is no serializer to agree on.

The verifier signs nothing and **recomputes** `manifest_sha256` from the manifest
bytes before checking — a seal that names its own digest cannot authenticate a
manifest it does not describe.

### Keyring

```json
{
  "schema_version": 1,
  "keys": [
    {
      "key_id": "decoda-evidence-2026-01",
      "algorithm": "Ed25519",
      "public_key": "<base64 of the raw 32-byte public key, or a PEM block>",
      "status": "active"
    }
  ]
}
```

Published at `https://<your-decoda-host>/.well-known/decoda-evidence-keys.json`.
Pin it once; verification itself never touches the network.

Retired keys keep `status: "retired"` and are **never removed** — historical
evidence must stay verifiable after rotation.

---

## Trust model

The keyring inside an evidence ZIP is a **convenience copy**. A public key carried
inside the package it verifies establishes no trust on its own: whoever could alter
the package could alter the bundled key with it. That is why
`--allow-bundled-keyring` is opt-in and the CLI always prints which key source it
used.

Obtain the keyring from Decoda's `.well-known` URL, from a contract annex, or from
any channel you trust — then pin it locally and verify forever offline.

---

## What offline verification does NOT prove

* that any source event was truthful when it was ingested;
* that a blockchain, RPC provider or data feed reported honestly;
* that an off-chain system was correct;
* Decoda's entire server-side audit chain — only the single anchor value the
  package records.

---

## Security properties of the verifier itself

* It cannot sign. There is no private-key code path.
* It contains no Decoda secret and reads no Decoda environment variable.
* It never modifies the package and never extracts files to disk.
* It hashes members by streaming them out of the archive.
* It rejects path traversal, absolute paths, drive letters, control characters,
  symlink entries, duplicate archive names, duplicate JSON keys, oversized members
  and implausible compression ratios — before using any content.
* It prints paths and reasons only, never evidence content.

A compromise of this verifier does not enable evidence forgery.

### One thing it cannot detect

The seal is a detached document, so it is not covered by `manifest_sha256`. Anyone
can strip the `signatures` block and leave the HMAC. That cannot forge anything —
it only downgrades the report from `Authenticity: VERIFIED` to
`NOT INDEPENDENTLY VERIFIABLE`. No edit to a package can produce a verified
authenticity result without the private key.

If you expected a signed package and see "not independently verifiable", treat it
as a red flag and request the package again.

---

## Test vectors

`testvectors/evidence-test-vectors.json` pins canonical JSON bytes, file digests,
the manifest digest, Merkle leaves and root, the exact signed payload and a
signature. Three independent implementations assert against it — backend
generation, backend verification and this verifier — so a silent format drift
fails a test before it can break evidence in the field.

Regenerate only when the format intentionally changes:

```bash
python tools/decoda_evidence_verifier/testvectors/generate.py
```

The Ed25519 keypair in the vectors is derived deterministically from a published
constant. It is a test fixture, not a Decoda signing key, and nothing about it is
secret.
