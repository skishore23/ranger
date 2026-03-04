"""Test guard gating functionality.

Ensures guards properly block risky operations until violations are resolved.
"""
from topology import register_region, clear_registry
from topology.types import Atom
from regions.guard_pii import GuardPII
from topology.stitch import repair_context


class TestGuardGating:
    """Test guard gating and violation handling."""
    
    def setup_method(self):
        """Set up test environment."""
        clear_registry()
        register_region(GuardPII("guard.pii", "mask"))
    
    def teardown_method(self):
        """Clean up after test."""
        clear_registry()
    
    def test_pii_detection(self):
        """Test PII detection in atoms."""
        from topology.registry import get_region
        
        guard = get_region("guard.pii")
        assert guard is not None
        
        # Test atoms with PII
        pii_atoms = [
            Atom(
                id="email-atom",
                modality="text",
                content="Contact me at john.doe@example.com for details",
                schema="test.schema@v1",
                facets={"domain": "test", "ts": 1234567890},
                provenance={},
                policy={}
            ),
            Atom(
                id="phone-atom", 
                modality="text",
                content="Call me at 555-123-4567",
                schema="test.schema@v1",
                facets={"domain": "test", "ts": 1234567890},
                provenance={},
                policy={}
            ),
            Atom(
                id="ssn-atom",
                modality="text", 
                content="SSN: 123-45-6789",
                schema="test.schema@v1",
                facets={"domain": "test", "ts": 1234567890},
                provenance={},
                policy={}
            )
        ]
        
        # Validate atoms
        result = guard.validate(pii_atoms)
        
        # Should detect violations
        assert not result["ok"]
        assert len(result["findings"]) > 0
        
        # Check finding types
        finding_types = {finding.content["pii_type"] for finding in result["findings"]}
        assert "email" in finding_types
        assert "phone" in finding_types
        assert "ssn" in finding_types
    
    def test_pii_redaction(self):
        """Test PII redaction in atoms."""
        from topology.registry import get_region
        
        guard = get_region("guard.pii")
        assert guard is not None
        
        # Test atom with PII
        atom = Atom(
            id="pii-atom",
            modality="text",
            content="Email: test@example.com, Phone: 555-123-4567",
            schema="test.schema@v1",
            facets={"domain": "test", "ts": 1234567890},
            provenance={},
            policy={}
        )
        
        # Summarize with redaction
        goal = {"name": "test-goal", "domain": "test"}
        summary = guard.summarize([atom], goal)
        
        # Summary should indicate redaction was applied
        assert summary.content["redacted_atoms"] > 0
        assert summary.content["pii_types_found"]
    
    def test_guard_blocking(self):
        """Test that guards block operations when violations exist."""
        from topology.registry import get_region
        
        guard = get_region("guard.pii")
        assert guard is not None
        
        # Create atoms with PII
        risky_atoms = [
            Atom(
                id="risky-atom",
                modality="text",
                content="Sensitive data: john@example.com",
                schema="test.schema@v1",
                facets={"domain": "test", "ts": 1234567890},
                provenance={},
                policy={}
            )
        ]
        
        # Validate - should fail
        result = guard.validate(risky_atoms)
        assert not result["ok"]
        assert len(result["findings"]) > 0
        
        # Repair context
        repaired = repair_context(risky_atoms, result, guard)
        
        # Repaired atoms should have redaction applied
        assert len(repaired) > 0
        for atom in repaired:
            if atom.policy.get("redacted"):
                assert "[EMAIL_REDACTED]" in str(atom.content)
    
    def test_guard_allowlist(self):
        """Test that guards allow clean atoms through."""
        from topology.registry import get_region
        
        guard = get_region("guard.pii")
        assert guard is not None
        
        # Create clean atoms
        clean_atoms = [
            Atom(
                id="clean-atom-1",
                modality="text",
                content="This is clean content with no PII",
                schema="test.schema@v1",
                facets={"domain": "test", "ts": 1234567890},
                provenance={},
                policy={}
            ),
            Atom(
                id="clean-atom-2",
                modality="code",
                content="def hello_world():\n    print('Hello, World!')",
                schema="test.schema@v1",
                facets={"domain": "test", "ts": 1234567890},
                provenance={},
                policy={}
            )
        ]
        
        # Validate - should pass
        result = guard.validate(clean_atoms)
        assert result["ok"]
        assert len(result["findings"]) == 0
    
    def test_guard_severity_levels(self):
        """Test different PII severity levels."""
        from topology.registry import get_region
        
        guard = get_region("guard.pii")
        assert guard is not None
        
        # Test different PII types with different severities
        test_cases = [
            ("SSN: 123-45-6789", "ssn", "high"),
            ("Card: 1234-5678-9012-3456", "credit_card", "high"),
            ("Email: test@example.com", "email", "medium"),
            ("Phone: 555-123-4567", "phone", "medium"),
            ("IP: 192.168.1.1", "ip_address", "low"),
            ("URL: https://example.com", "url", "low")
        ]
        
        for content, expected_type, expected_severity in test_cases:
            atom = Atom(
                id=f"test-{expected_type}",
                modality="text",
                content=content,
                schema="test.schema@v1",
                facets={"domain": "test", "ts": 1234567890},
                provenance={},
                policy={}
            )
            
            result = guard.validate([atom])
            assert not result["ok"]
            assert len(result["findings"]) == 1
            
            finding = result["findings"][0]
            assert finding.content["pii_type"] == expected_type
            assert finding.content["severity"] == expected_severity
    
    def test_guard_reconciliation(self):
        """Test guard reconciliation preferences."""
        from topology.registry import get_region
        
        guard = get_region("guard.pii")
        assert guard is not None
        
        # Create atoms with different redaction levels
        unredacted = Atom(
            id="unredacted",
            modality="text",
            content="test@example.com",
            schema="test.schema@v1",
            facets={"domain": "test", "ts": 1234567890},
            provenance={},
            policy={"redacted": False}
        )
        
        redacted = Atom(
            id="redacted",
            modality="text",
            content="[EMAIL_REDACTED]",
            schema="test.schema@v1",
            facets={"domain": "test", "ts": 1234567890},
            provenance={},
            policy={"redacted": True}
        )
        
        goal = {"name": "test-goal", "domain": "test"}
        
        # Reconcile - should prefer stricter redaction
        success, reconciled, reason = guard.reconcile(unredacted, redacted, goal)
        
        assert success
        assert reconciled is not None
        assert reason == "stricter_redaction_wins"
        assert reconciled.policy.get("redacted", False)


    def test_finding_ids_are_stable(self):
        """Test finding IDs are deterministic for same input."""
        from topology.registry import get_region

        guard = get_region("guard.pii")
        assert guard is not None

        atom = Atom(
            id="stable",
            modality="text",
            content="Email me at stable@example.com",
            schema="test.schema@v1",
            facets={"domain": "test", "ts": 1234567890},
            provenance={},
            policy={}
        )

        first = guard.validate([atom])["findings"]
        second = guard.validate([atom])["findings"]

        assert len(first) == len(second) == 1
        assert first[0].id == second[0].id
