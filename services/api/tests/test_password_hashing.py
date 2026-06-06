from __future__ import annotations

from services.api.app.pilot import hash_password, verify_password


def test_hash_uses_new_cost_factor():
    encoded = hash_password('MySecret123')
    # Format: scrypt$<log2n>$<r>$<p>$<salt>$<digest>
    parts = encoded.split('$')
    assert parts[0] == 'scrypt'
    assert len(parts) == 6
    assert parts[1] == '16'   # log2(2**16)
    assert parts[2] == '8'    # r
    assert parts[3] == '1'    # p


def test_new_format_verifies_correctly():
    password = 'EnterpriseSaas99!'
    encoded = hash_password(password)
    assert verify_password(password, encoded) is True


def test_new_format_rejects_wrong_password():
    encoded = hash_password('CorrectHorse99')
    assert verify_password('WrongHorse99', encoded) is False


def _make_legacy_hash(password: str) -> str:
    import hashlib
    import secrets
    import base64

    def b64url(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).decode().rstrip('=')

    salt = secrets.token_bytes(16)
    # Legacy n=2**14 fits in 32MB default limit
    digest = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=2**14, r=8, p=1)
    return f'scrypt${b64url(salt)}${b64url(digest)}'


def test_legacy_format_still_verifies():
    password = 'LegacyPass123'
    legacy_encoded = _make_legacy_hash(password)
    assert verify_password(password, legacy_encoded) is True


def test_legacy_format_rejects_wrong_password():
    legacy_encoded = _make_legacy_hash('right')
    assert verify_password('wrong', legacy_encoded) is False


def test_verify_rejects_unknown_scheme():
    assert verify_password('pass', 'bcrypt$some$hash') is False


def test_verify_rejects_malformed_hash():
    assert verify_password('pass', 'scrypt$onlytwoparts') is False
    assert verify_password('pass', '') is False


def test_each_hash_is_unique():
    password = 'SamePassword1!'
    h1 = hash_password(password)
    h2 = hash_password(password)
    assert h1 != h2  # different salts
    assert verify_password(password, h1) is True
    assert verify_password(password, h2) is True
