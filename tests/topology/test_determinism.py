"""Test determinism of topology system.

Ensures same inputs/seed produce identical trace/digests.
"""
import time
from topology import register_region, clear_registry
from topology.types import Atom, Budget
from regions.mem_sqlite import MemSQLite
from regions.guard_pii import GuardPII


class TestDeterminism:
    """Test deterministic behavior of topology system."""
    
    def setup_method(self):
        """Set up test environment."""
        clear_registry()
        
        # Register test regions
        register_region(MemSQLite("mem.test", ":memory:", "test"))
        register_region(GuardPII("guard.test", "mask"))
        # Note: LLM and Tool regions require credentials, so we'll test without them
        
    def teardown_method(self):
        """Clean up after test."""
        clear_registry()
    
    def test_atom_id_determinism(self):
        """Test that atom IDs are deterministic."""
        from topology.attest import create_atom_id
        
        content = "test content"
        schema = "test.schema@v1"
        facets = {"domain": "test", "ts": 1234567890}
        
        # Create same atom multiple times
        id1 = create_atom_id(content, schema, facets)
        id2 = create_atom_id(content, schema, facets)
        
        assert id1 == id2
        assert id1.startswith("sha256:")
    
    def test_atom_signing_determinism(self):
        """Test that atom signing is deterministic."""
        from topology.attest import sign_atom, verify_atom
        
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
        
        # Sign atom multiple times
        signed1 = sign_atom(atom, secret_key)
        signed2 = sign_atom(atom, secret_key)
        
        # Signatures should be identical
        assert signed1.provenance["sig"] == signed2.provenance["sig"]
        
        # Verification should work
        assert verify_atom(signed1, secret_key)
        assert verify_atom(signed2, secret_key)
    
    def test_planner_determinism(self):
        """Test that planner produces deterministic paths."""
        from topology.planner import plan_path
        
        class MockUnit:
            def __init__(self):
                self.id = "test-unit"
        
        class MockGoal:
            def __init__(self):
                self.name = "test-goal"
                self.domain = "test"
        
        unit = MockUnit()
        goal = MockGoal()
        budget = Budget(tokens=1000, ms=10000, calls=5)
        
        # Plan path multiple times
        path1 = plan_path(unit, goal, budget)
        path2 = plan_path(unit, goal, budget)
        
        # Paths should be identical
        assert path1.cost == path2.cost
        assert len(path1.memory_like) == len(path2.memory_like)
        assert len(path1.guards) == len(path2.guards)
        assert len(path1.models) == len(path2.models)
        assert len(path1.tools) == len(path2.tools)
    
    def test_packer_determinism(self):
        """Test that packer produces deterministic context windows."""
        from topology.packer import pack_context
        
        # Create test atoms
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
            for i in range(10)
        ]
        
        budget = Budget(tokens=100, ms=1000, calls=5)
        goal = {"name": "test-goal", "domain": "test"}
        
        # Pack multiple times
        window1 = pack_context(atoms, budget, goal)
        window2 = pack_context(atoms, budget, goal)
        
        # Windows should be identical
        assert window1.utility_score == window2.utility_score
        assert len(window1.atoms) == len(window2.atoms)
        assert window1.budget_used.tokens == window2.budget_used.tokens
    
    def test_stitch_determinism(self):
        """Test that stitch operations are deterministic."""
        from topology.stitch import summarize_regions
        
        # Create test atoms
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
        
        goal = {"name": "test-goal", "domain": "test"}
        
        # Get memory region for summarization
        from topology.registry import get_region
        memory_region = get_region("mem.test")
        
        if memory_region:
            # Summarize multiple times
            summaries1 = summarize_regions([memory_region], atoms, goal)
            summaries2 = summarize_regions([memory_region], atoms, goal)
            
            # Summaries should be identical
            assert len(summaries1) == len(summaries2)
            if summaries1:
                assert summaries1[0].id == summaries2[0].id
    
    def test_region_operations_determinism(self):
        """Test that region operations are deterministic."""
        from topology.registry import get_region
        
        memory_region = get_region("mem.test")
        guard_region = get_region("guard.test")
        
        if memory_region:
            # Test memory region determinism
            atoms = [
                Atom(
                    id="test-atom",
                    modality="text",
                    content="test content",
                    schema="test.schema@v1",
                    facets={"domain": "test", "ts": int(time.time() * 1000)},
                    provenance={},
                    policy={}
                )
            ]
            
            # Write atoms multiple times
            memory_region.write(atoms)
            memory_region.write(atoms)
            
            # Read should be deterministic
            query = {"domain": "test"}
            results1 = list(memory_region.read(query))
            results2 = list(memory_region.read(query))
            
            assert len(results1) == len(results2)
            if results1:
                assert results1[0].id == results2[0].id
        
        if guard_region:
            # Test guard region determinism
            atoms = [
                Atom(
                    id="test-atom",
                    modality="text",
                    content="test@example.com",  # Contains PII
                    schema="test.schema@v1",
                    facets={"domain": "test", "ts": int(time.time() * 1000)},
                    provenance={},
                    policy={}
                )
            ]
            
            # Validate multiple times
            result1 = guard_region.validate(atoms)
            result2 = guard_region.validate(atoms)
            
            # Results should be identical
            assert result1["ok"] == result2["ok"]
            assert len(result1["findings"]) == len(result2["findings"])
