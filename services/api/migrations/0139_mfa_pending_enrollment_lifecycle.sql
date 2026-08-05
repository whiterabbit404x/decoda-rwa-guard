-- Migration 0139: Give TOTP MFA enrollment a proper pending-enrollment lifecycle
-- (stable enrollment id + expiry) and invalidate the compromised/never-usable
-- pending secrets left behind by the confirmation bug.
--
-- Root cause of "MFA verification failed: Invalid MFA code" (fixed in pilot.py):
--   secret_crypto.encrypt_secret() emits the `aes256gcm:v2:` scheme, but
--   _decrypt_mfa_secret() only recognized `aes256gcm:v1:` / `legacy_b64:`. A v2
--   pending secret therefore fell through to the "pre-0092 unencrypted" branch and
--   was handed to the TOTP verifier as raw ciphertext, so every confirmation failed
--   in any environment with a real SECRET_ENCRYPTION_KEY (production/staging). The
--   otpauth URI advertised the true base32 secret while confirmation verified against
--   the undecrypted blob — enrollment and confirmation effectively used different
--   secrets.
--
-- This migration (paired with the pilot.py changes):
--   1. Adds a stable pending-enrollment identity so confirmation can bind to the
--      exact enrollment it is completing:
--        * mfa_pending_enrollment_id  — id returned by Enroll, echoed by Confirm.
--        * mfa_pending_created_at      — when the pending secret was minted.
--        * mfa_pending_expires_at      — short TTL; an abandoned/exposed pending
--                                        secret cannot be activated after it lapses.
--      Re-enrolling overwrites these columns, which explicitly invalidates the prior
--      pending enrollment (its id no longer matches, so a stale authenticator entry
--      cannot be confirmed).
--   2. Invalidates every existing pending secret. These are compromised (the pending
--      secret + OTP were exposed in screenshots during testing) and/or are the broken
--      v2 blobs that never verified. Clearing them forces a fresh, correctly
--      round-tripping enrollment. Activated MFA (mfa_totp_secret / mfa_enabled_at) is
--      left untouched — only in-flight pending enrollments are reset.
--
-- Truthfulness / safety (CLAUDE.md):
--   * No activated MFA is disabled and no secret is logged or exposed here.
--   * Only pending (not-yet-confirmed) enrollments are invalidated.
--
-- Idempotent: additive columns use IF NOT EXISTS; the reset only touches rows that
-- still carry a pending secret, so re-running is a no-op.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS mfa_pending_enrollment_id UUID NULL,
    ADD COLUMN IF NOT EXISTS mfa_pending_created_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS mfa_pending_expires_at TIMESTAMPTZ NULL;

-- ---------------------------------------------------------------------------
-- Invalidate the compromised / never-usable pending enrollments.
-- ---------------------------------------------------------------------------
DO $mig$
DECLARE
    v_reset int := 0;
BEGIN
    UPDATE users
    SET mfa_pending_secret = NULL,
        mfa_pending_enrollment_id = NULL,
        mfa_pending_created_at = NULL,
        mfa_pending_expires_at = NULL,
        updated_at = NOW()
    WHERE mfa_pending_secret IS NOT NULL;

    GET DIAGNOSTICS v_reset = ROW_COUNT;
    RAISE NOTICE '0139 mfa pending reset: % compromised/stale pending enrollment(s) invalidated', v_reset;
END;
$mig$;
