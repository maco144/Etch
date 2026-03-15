"""Tests for the MMR audit chain."""
from etch.chain import AuditChain, ChainEntry, InclusionProof, verify_inclusion_proof


class TestAuditChain:

    def test_genesis_state(self):
        chain = AuditChain()
        assert chain.leaf_count() == 0
        assert chain.current_root() == "0" * 64

    def test_append_increments_count(self):
        chain = AuditChain()
        chain.append("test", {"key": "value"})
        assert chain.leaf_count() == 1

    def test_append_changes_root(self):
        chain = AuditChain()
        genesis = chain.current_root()
        chain.append("test", {"key": "value"})
        assert chain.current_root() != genesis

    def test_append_returns_chain_entry(self):
        chain = AuditChain()
        entry = chain.append("content_proof", {"content_hash": "abc"})
        assert isinstance(entry, ChainEntry)
        assert entry.leaf_index == 0
        assert entry.action_type == "content_proof"
        assert len(entry.leaf_hash) == 64
        assert len(entry.mmr_root) == 64

    def test_sequential_entries_form_chain(self):
        chain = AuditChain()
        e1 = chain.append("a", {"x": 1})
        e2 = chain.append("b", {"x": 2})
        assert e2.leaf_index == 1
        assert e2.mmr_root != e1.mmr_root

    def test_deterministic_payload_hash(self):
        chain = AuditChain()
        e1 = chain.append("test", {"a": 1, "b": 2})
        chain2 = AuditChain()
        e2 = chain2.append("test", {"b": 2, "a": 1})  # different key order
        assert e1.payload_hash == e2.payload_hash  # sort_keys=True


class TestInclusionProof:

    def test_generate_and_verify(self):
        chain = AuditChain()
        chain.append("setup", {"x": 1})
        chain.append("content_proof", {"content_hash": "abc123"})

        proof = chain.generate_proof(1)
        assert proof is not None
        assert isinstance(proof, InclusionProof)
        assert verify_inclusion_proof(proof) is True

    def test_genesis_entry_verifies(self):
        chain = AuditChain()
        chain.append("first", {"data": "hello"})
        proof = chain.generate_proof(0)
        assert proof is not None
        assert proof.prev_root == "0" * 64
        assert verify_inclusion_proof(proof) is True

    def test_tampered_proof_fails(self):
        chain = AuditChain()
        chain.append("test", {"x": 1})
        proof = chain.generate_proof(0)
        # Tamper with the leaf hash
        tampered = InclusionProof(
            leaf_index=proof.leaf_index,
            leaf_hash="f" * 64,  # tampered
            mmr_root=proof.mmr_root,
            prev_root=proof.prev_root,
            action_type=proof.action_type,
            payload_hash=proof.payload_hash,
            timestamp=proof.timestamp,
        )
        assert verify_inclusion_proof(tampered) is False

    def test_out_of_range_returns_none(self):
        chain = AuditChain()
        assert chain.generate_proof(0) is None
        assert chain.generate_proof(-1) is None

    def test_proof_to_dict(self):
        chain = AuditChain()
        chain.append("test", {})
        proof = chain.generate_proof(0)
        d = proof.to_dict()
        assert set(d.keys()) == {
            "leaf_index", "leaf_hash", "mmr_root", "prev_root",
            "action_type", "payload_hash", "timestamp",
        }


class TestVerifyEntry:

    def test_valid_entry(self):
        chain = AuditChain()
        entry = chain.append("test", {"val": 42})
        assert chain.verify_entry(entry, "0" * 64) is True

    def test_invalid_prev_root(self):
        chain = AuditChain()
        entry = chain.append("test", {"val": 42})
        assert chain.verify_entry(entry, "1" * 64) is False
