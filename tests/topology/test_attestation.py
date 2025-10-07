"""Test attestation functionality.

Ensures atom signing, verification, and audit trail work correctly.
"""
import time
from topology.types import Atom
from topology.attest import (
    create_atom_id,
    create_provenance_chain,
    extract_audit_trail,
    sign_atom,
    verify_atom,
    verify_atom_chain,
)


class TestAttestation:
    """Test atom attestation and verification."""
    
    def test_atom_id_creation(self):
        """Test deterministic atom ID creation."""
        content = "test content"
        schema = "test.schema@v1"
        facets = {"domain": "test", "ts": 1234567890}
        
        # Create ID multiple times
        id1 = create_atom_id(content, schema, facets)
        id2 = create_atom_id(content, schema, facets)
        
        # Should be identical
        assert id1 == id2
        assert id1.startswith("sha256:")
        assert len(id1) == 71  # "sha256:" + 64 hex chars
    
    def test_atom_signing(self):
        """Test atom signing with secret key."""
        atom = Atom(
            id="test-atom",
            modality="text",
            content="test content",
            schema="test.schema@v1",
            facets={"domain": "test", "ts": 1234567890},
            provenance={},
            policy={}
        )
        
        secret_key = "test-secret-key"
        
        # Sign atom
        signed = sign_atom(atom, secret_key)
        
        # Should have signature in provenance
        assert "sig" in signed.provenance
        assert "signed_at" in signed.provenance
        assert "signature_version" in signed.provenance
        
        # Original atom should be unchanged
        assert atom.provenance == {}
        
        # Signed atom should have same content
        assert signed.id == atom.id
        assert signed.content == atom.content
        assert signed.schema == atom.schema
        assert signed.facets == atom.facets
        assert signed.policy == atom.policy
    
    def test_atom_verification(self):
        """Test atom signature verification."""
        atom = Atom(
            id="test-atom",
            modality="text",
            content="test content",
            schema="test.schema@v1",
            facets={"domain": "test", "ts": 1234567890},
            provenance={},
            policy={}
        )
        
        secret_key = "test-secret-key"
        wrong_key = "wrong-secret-key"
        
        # Sign atom
        signed = sign_atom(atom, secret_key)
        
        # Verify with correct key
        assert verify_atom(signed, secret_key)
        
        # Verify with wrong key should fail
        assert not verify_atom(signed, wrong_key)
        
        # Verify unsigned atom should fail
        assert not verify_atom(atom, secret_key)
    
    def test_atom_chain_verification(self):
        """Test verification of atom chains."""
        # Create chain of atoms
        atoms = [
            Atom(
                id=f"atom-{i}",
                modality="text",
                content=f"content {i}",
                schema="test.schema@v1",
                facets={"domain": "test", "ts": int(time.time() * 1000) + i},
                provenance={},
                policy={}
            )
            for i in range(5)
        ]
        
        secret_key = "test-secret-key"
        
        # Sign all atoms
        signed_atoms = [sign_atom(atom, secret_key) for atom in atoms]
        
        # Verify chain
        result = verify_atom_chain(signed_atoms, secret_key)
        
        assert result["valid"]
        assert len(result["invalid_atoms"]) == 0
        assert len(result["missing_signatures"]) == 0
        assert result["chain_integrity"]
    
    def test_atom_chain_with_invalid_signature(self):
        """Test chain verification with invalid signature."""
        # Create atoms
        atoms = [
            Atom(
                id=f"atom-{i}",
                modality="text",
                content=f"content {i}",
                schema="test.schema@v1",
                facets={"domain": "test", "ts": int(time.time() * 1000) + i},
                provenance={},
                policy={}
            )
            for i in range(3)
        ]
        
        secret_key = "test-secret-key"
        wrong_key = "wrong-secret-key"
        
        # Sign first two with correct key, third with wrong key
        signed_atoms = [
            sign_atom(atoms[0], secret_key),
            sign_atom(atoms[1], secret_key),
            sign_atom(atoms[2], wrong_key)
        ]
        
        # Verify chain
        result = verify_atom_chain(signed_atoms, secret_key)
        
        assert not result["valid"]
        assert len(result["invalid_atoms"]) == 1
        assert result["invalid_atoms"][0]["atom_id"] == "atom-2"
        assert result["invalid_atoms"][0]["reason"] == "invalid_signature"
    
    def test_provenance_chain_creation(self):
        """Test creation of provenance chains."""
        atoms = [
            Atom(
                id=f"atom-{i}",
                modality="text",
                content=f"content {i}",
                schema="test.schema@v1",
                facets={"domain": "test", "ts": int(time.time() * 1000) + i},
                provenance={},
                policy={}
            )
            for i in range(3)
        ]
        
        # Create provenance chain
        chained = create_provenance_chain(atoms)
        
        # Check chain structure
        assert len(chained) == 3
        
        # First atom should have no parents
        assert chained[0].provenance["parents"] == []
        assert chained[0].provenance["chain_position"] == 0
        assert chained[0].provenance["chain_length"] == 3
        
        # Second atom should have first as parent
        assert chained[1].provenance["parents"] == ["atom-0"]
        assert chained[1].provenance["chain_position"] == 1
        assert chained[1].provenance["chain_length"] == 3
        
        # Third atom should have first two as parents
        assert set(chained[2].provenance["parents"]) == {"atom-0", "atom-1"}
        assert chained[2].provenance["chain_position"] == 2
        assert chained[2].provenance["chain_length"] == 3
    
    def test_audit_trail_extraction(self):
        """Test audit trail extraction from atom chain."""
        atoms = [
            Atom(
                id=f"atom-{i}",
                modality="text",
                content=f"content {i}",
                schema="test.schema@v1",
                facets={
                    "domain": "test",
                    "ts": int(time.time() * 1000) + i,
                    "source": f"source-{i}",
                    "trust": 0.5 + i * 0.1
                },
                provenance={
                    "parents": [f"atom-{j}" for j in range(i)],
                    "cost": {"tokens_in": 10, "tokens_out": 20, "ms": 100}
                },
                policy={"pii": False, "acl": []}
            )
            for i in range(3)
        ]
        
        # Extract audit trail
        trail = extract_audit_trail(atoms)
        
        # Check trail structure
        assert len(trail) == 3
        
        for i, entry in enumerate(trail):
            assert entry["atom_id"] == f"atom-{i}"
            assert entry["modality"] == "text"
            assert entry["schema"] == "test.schema@v1"
            assert entry["source"] == f"source-{i}"
            assert entry["domain"] == "test"
            assert entry["trust"] == 0.5 + i * 0.1
            assert entry["parents"] == [f"atom-{j}" for j in range(i)]
            assert "cost" in entry
            assert "policy" in entry
    
    def test_signature_tampering_detection(self):
        """Test detection of signature tampering."""
        atom = Atom(
            id="test-atom",
            modality="text",
            content="test content",
            schema="test.schema@v1",
            facets={"domain": "test", "ts": 1234567890},
            provenance={},
            policy={}
        )
        
        secret_key = "test-secret-key"
        
        # Sign atom
        signed = sign_atom(atom, secret_key)
        
        # Tamper with content
        tampered = Atom(
            id=signed.id,
            modality=signed.modality,
            content="tampered content",  # Changed content
            schema=signed.schema,
            facets=signed.facets,
            provenance=signed.provenance,  # Same signature
            policy=signed.policy
        )
        
        # Verification should fail
        assert not verify_atom(tampered, secret_key)
    
    def test_different_secret_keys(self):
        """Test that different secret keys produce different signatures."""
        atom = Atom(
            id="test-atom",
            modality="text",
            content="test content",
            schema="test.schema@v1",
            facets={"domain": "test", "ts": 1234567890},
            provenance={},
            policy={}
        )
        
        key1 = "secret-key-1"
        key2 = "secret-key-2"
        
        # Sign with different keys
        signed1 = sign_atom(atom, key1)
        signed2 = sign_atom(atom, key2)
        
        # Signatures should be different
        assert signed1.provenance["sig"] != signed2.provenance["sig"]
        
        # Each should only verify with its own key
        assert verify_atom(signed1, key1)
        assert not verify_atom(signed1, key2)
        assert verify_atom(signed2, key2)
        assert not verify_atom(signed2, key1)
