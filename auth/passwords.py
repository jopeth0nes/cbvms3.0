"""Versioned password hashes; legacy SHA-256 is verification-only.

PBKDF2-HMAC-SHA256, 600,000 iterations, random 128-bit salt. Passwords are
never normalized, trimmed or truncated. See PASSWORD_SETUP.md.
"""
import hashlib
import hmac
import secrets

ITERATIONS = 600_000
MIN_PASSWORD_LENGTH = 8
# Historical/common bootstrap credentials, plus the project's staff defaults.
DEFAULT_PASSWORDS = frozenset({'password', 'password123', 'student123', 'admin123', 'superadmin123'})


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, ITERATIONS)
    return f'pbkdf2_sha256$v1${ITERATIONS}${salt.hex()}${digest.hex()}'


def verify_password(password, encoded):
    if not isinstance(password, str) or not isinstance(encoded, str):
        return False
    try:
        if encoded.startswith('pbkdf2_sha256$v1$'):
            _, _, iterations, salt, expected = encoded.split('$')
            rounds = int(iterations)
            if not 100_000 <= rounds <= 2_000_000:
                return False
            actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), rounds)
            return hmac.compare_digest(actual.hex(), expected)
        if len(encoded) == 64:
            return hmac.compare_digest(hashlib.sha256(password.encode('utf-8')).hexdigest(), encoded)
    except (ValueError, TypeError):
        pass
    return False


def validate_new_password(password, confirmation, *, username='', student_id=''):
    if not isinstance(password, str) or not isinstance(confirmation, str):
        raise ValueError('Enter and confirm your new password.')
    if password != confirmation:
        raise ValueError('New password and confirmation do not match.')
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError('Use at least eight characters. Longer passphrases are welcome.')
    if password.casefold() in DEFAULT_PASSWORDS or password in {username, student_id}:
        raise ValueError('Choose a personal password, not a default credential or your account ID.')
