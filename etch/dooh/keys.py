"""
ed25519 keypairs for DOOH receipts.

Each player and venue has one keypair. Private keys are stored as raw 32-byte
seeds (base64url-encoded on disk). Public keys travel as base64url strings
in the IdentityManifest.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


@dataclass
class KeyPair:
    """An ed25519 keypair with a stable `key_id` (e.g. 'screen:nyc-01#k1')."""

    key_id: str
    _private: Ed25519PrivateKey
    _public: Ed25519PublicKey

    @classmethod
    def generate(cls, key_id: str) -> "KeyPair":
        priv = Ed25519PrivateKey.generate()
        return cls(key_id=key_id, _private=priv, _public=priv.public_key())

    @classmethod
    def from_seed(cls, key_id: str, seed: bytes) -> "KeyPair":
        if len(seed) != 32:
            raise ValueError("ed25519 seed must be 32 bytes")
        priv = Ed25519PrivateKey.from_private_bytes(seed)
        return cls(key_id=key_id, _private=priv, _public=priv.public_key())

    @classmethod
    def load(cls, path: str | Path) -> "KeyPair":
        import json
        data = json.loads(Path(path).read_text())
        if "key_id" not in data or "seed" not in data:
            raise ValueError(f"Invalid key file at {path}")
        return cls.from_seed(data["key_id"], _b64url_decode(data["seed"]))

    def save(self, path: str | Path) -> None:
        import json
        seed = self._private.private_bytes_raw()
        Path(path).write_text(json.dumps({"key_id": self.key_id, "seed": _b64url_encode(seed)}))
        Path(path).chmod(0o600)

    @property
    def public_b64(self) -> str:
        return _b64url_encode(self._public.public_bytes_raw())

    def sign(self, message: bytes) -> bytes:
        return self._private.sign(message)


def verify_signature(public_b64: str, message: bytes, signature: bytes) -> bool:
    """Verify an ed25519 signature using a base64url-encoded public key."""
    try:
        pub = Ed25519PublicKey.from_public_bytes(_b64url_decode(public_b64))
        pub.verify(signature, message)
        return True
    except (InvalidSignature, ValueError):
        return False


def b64url_encode(b: bytes) -> str:
    return _b64url_encode(b)


def b64url_decode(s: str) -> bytes:
    return _b64url_decode(s)
