"""
Identity manifest — advertiser-published mapping of key_id → public key.

The advertiser, not a global PKI, is the trust anchor for screen and venue
identity. Verifiers fetch the manifest from the advertiser at verification
time and use it to resolve the key_ids in a SignedBundle.

For v0 the manifest is a plain JSON file. v1 will anchor manifest hashes in
Etch on every change so historical resolution stays auditable.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from pydantic import BaseModel, Field

MANIFEST_SCHEMA_VERSION = "dooh-manifest/1"


class IdentityManifest(BaseModel):
    """Maps `key_id` strings to base64url-encoded ed25519 public keys."""

    schema_version: str = MANIFEST_SCHEMA_VERSION
    advertiser_id: str
    issued_at: str  # RFC 3339
    screens: Dict[str, str] = Field(default_factory=dict)
    venues: Dict[str, str] = Field(default_factory=dict)

    def resolve(self, key_id: str) -> Optional[str]:
        """Return the base64url public key for `key_id`, or None if unknown."""
        return self.screens.get(key_id) or self.venues.get(key_id)

    def add_screen(self, key_id: str, public_b64: str) -> None:
        self.screens[key_id] = public_b64

    def add_venue(self, key_id: str, public_b64: str) -> None:
        self.venues[key_id] = public_b64

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.model_dump(), indent=2, sort_keys=True))

    @classmethod
    def load(cls, path: str | Path) -> "IdentityManifest":
        return cls.model_validate(json.loads(Path(path).read_text()))
