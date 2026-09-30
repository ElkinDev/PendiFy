"""The pairing value: the Worker's alphabet and length, a mint, normalization and the 4-4-4 display.

The rules are the Worker's own (proxy/src/link-registry.ts:31-32, :45 and :97-101), so a value this
program normalizes is the value the Worker normalizes and digests.
"""
import secrets

# 32 symbols with no 0, O, 1 or I (link-registry.ts:31); twelve of them are 2^60.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
LENGTH = 12
# A pasted value may carry the display dashes; anything longer cannot be one (link-registry.ts:45).
MAX_RAW_CHARS = 32


def mint():
    """Twelve symbols of the alphabet from the system's cryptographic random source."""
    return "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))


def normalize(value):
    """Upper-cased, every character outside the alphabet dropped; None unless exactly twelve remain."""
    if not isinstance(value, str) or len(value) > MAX_RAW_CHARS:
        return None
    code = "".join(char for char in value.upper() if char in ALPHABET)
    return code if len(code) == LENGTH else None


def display(code):
    """The value as 4-4-4. A value that does not normalize is refused and never echoed."""
    normal = normalize(code)
    if normal is None:
        raise ValueError("not a pairing value of twelve symbols")
    return f"{normal[0:4]}-{normal[4:8]}-{normal[8:12]}"
