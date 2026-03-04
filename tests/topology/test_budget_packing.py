"""Test budget packing functionality.

Ensures packer respects budget constraints and evicts low-utility atoms.
"""
import time
from topology import clear_registry
from topology.types import Atom, Budget
from topology.packer import pack_context


class TestBudgetPacking:
    """Test budget-constrained context window packing."""
    
    def setup_method(self):
        """Set up test environment."""
        clear_registry()
    
    def teardown_method(self):
        """Clean up after test."""
        clear_registry()
    
    def test_basic_packing(self):
        """Test basic context window packing."""
        # Create test atoms
        atoms = [
            Atom(
                id=f"atom-{i}",
                modality="text",
                content=f"Content {i}",
                schema="test.schema@v1",
                facets={"domain": "test", "ts": int(time.time() * 1000) + i},
                provenance={},
                policy={}
            )
            for i in range(10)
        ]
        
        budget = Budget(tokens=100, ms=1000, calls=5)
        goal = {"name": "test-goal", "domain": "test"}
        
        # Pack context
        window = pack_context(atoms, budget, goal)
        
        # Should respect budget
        assert window.budget_used.tokens <= budget.tokens
        assert window.budget_used.ms <= budget.ms
        assert window.budget_used.calls <= budget.calls
        
        # Should have some atoms
        assert len(window.atoms) > 0
        assert window.utility_score > 0
    
    def test_token_budget_constraint(self):
        """Test token budget constraint."""
        # Create atoms with varying content lengths
        atoms = [
            Atom(
                id=f"short-{i}",
                modality="text",
                content="short",
                schema="test.schema@v1",
                facets={"domain": "test", "ts": int(time.time() * 1000) + i},
                provenance={},
                policy={}
            )
            for i in range(5)
        ] + [
            Atom(
                id=f"long-{i}",
                modality="text",
                content="x" * 1000,  # Long content
                schema="test.schema@v1",
                facets={"domain": "test", "ts": int(time.time() * 1000) + i + 5},
                provenance={},
                policy={}
            )
            for i in range(3)
        ]
        
        # Small token budget
        budget = Budget(tokens=50, ms=10000, calls=10)
        goal = {"name": "test-goal", "domain": "test"}
        
        window = pack_context(atoms, budget, goal)
        
        # Should prefer shorter content
        assert window.budget_used.tokens <= budget.tokens
        assert len(window.atoms) <= len(atoms)
    
    def test_modality_caps(self):
        """Test modality-specific caps."""
        # Create atoms with different modalities
        atoms = [
            Atom(
                id=f"text-{i}",
                modality="text",
                content=f"text content {i}",
                schema="test.schema@v1",
                facets={"domain": "test", "ts": int(time.time() * 1000) + i},
                provenance={},
                policy={}
            )
            for i in range(10)
        ] + [
            Atom(
                id=f"code-{i}",
                modality="code",
                content=f"def func{i}(): pass",
                schema="test.schema@v1",
                facets={"domain": "test", "ts": int(time.time() * 1000) + i + 10},
                provenance={},
                policy={}
            )
            for i in range(5)
        ]
        
        # Set modality caps
        budget = Budget(
            tokens=1000,
            ms=10000,
            calls=20,
            by_modality={"text": 3, "code": 2}
        )
        goal = {"name": "test-goal", "domain": "test"}
        
        window = pack_context(atoms, budget, goal)
        
        # Count atoms by modality
        text_count = sum(1 for atom in window.atoms if atom.modality == "text")
        code_count = sum(1 for atom in window.atoms if atom.modality == "code")
        
        # Should respect modality caps
        assert text_count <= 3
        assert code_count <= 2
    
    def test_utility_ranking(self):
        """Test utility-based atom ranking."""
        # Create atoms with different utility characteristics
        high_trust_atom = Atom(
            id="high-trust",
            modality="text",
            content="high trust content",
            schema="test.schema@v1",
            facets={"domain": "test", "ts": int(time.time() * 1000), "trust": 0.9},
            provenance={},
            policy={}
        )
        
        low_trust_atom = Atom(
            id="low-trust",
            modality="text",
            content="low trust content",
            schema="test.schema@v1",
            facets={"domain": "test", "ts": int(time.time() * 1000) - 1000, "trust": 0.1},
            provenance={},
            policy={}
        )
        
        recent_atom = Atom(
            id="recent",
            modality="text",
            content="recent content",
            schema="test.schema@v1",
            facets={"domain": "test", "ts": int(time.time() * 1000) + 1000, "trust": 0.5},
            provenance={},
            policy={}
        )
        
        atoms = [high_trust_atom, low_trust_atom, recent_atom]
        
        budget = Budget(tokens=50, ms=1000, calls=5)
        goal = {"name": "test-goal", "domain": "test"}
        
        window = pack_context(atoms, budget, goal)
        
        # Should prefer high-utility atoms
        selected_ids = {atom.id for atom in window.atoms}
        
        # High trust and recent atoms should be preferred
        assert "high-trust" in selected_ids or "recent" in selected_ids
    
    def test_empty_atoms(self):
        """Test packing with empty atom list."""
        atoms = []
        budget = Budget(tokens=100, ms=1000, calls=5)
        goal = {"name": "test-goal", "domain": "test"}
        
        window = pack_context(atoms, budget, goal)
        
        # Should handle empty input gracefully
        assert len(window.atoms) == 0
        assert window.utility_score == 0.0
        assert window.budget_used.tokens == 0
        assert window.budget_used.ms == 0
        assert window.budget_used.calls == 0
    
    def test_zero_budget(self):
        """Test packing with zero budget."""
        atoms = [
            Atom(
                id="atom-1",
                modality="text",
                content="content",
                schema="test.schema@v1",
                facets={"domain": "test", "ts": int(time.time() * 1000)},
                provenance={},
                policy={}
            )
        ]
        
        budget = Budget(tokens=0, ms=0, calls=0)
        goal = {"name": "test-goal", "domain": "test"}
        
        window = pack_context(atoms, budget, goal)
        
        # Should respect zero budget
        assert len(window.atoms) == 0
        assert window.budget_used.tokens == 0
        assert window.budget_used.ms == 0
        assert window.budget_used.calls == 0
    
    def test_large_budget(self):
        """Test packing with large budget."""
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
            for i in range(100)
        ]
        
        budget = Budget(tokens=100000, ms=100000, calls=1000)
        goal = {"name": "test-goal", "domain": "test"}
        
        window = pack_context(atoms, budget, goal)
        
        # Should include most or all atoms with large budget
        assert len(window.atoms) >= len(atoms) * 0.8  # At least 80% of atoms
        assert window.budget_used.tokens <= budget.tokens
        assert window.budget_used.ms <= budget.ms
        assert window.budget_used.calls <= budget.calls


    def test_custom_token_estimator(self):
        """Test pluggable token estimation."""
        atoms = [
            Atom(
                id="tiny",
                modality="text",
                content="tiny",
                schema="test.schema@v1",
                facets={"domain": "test", "ts": int(time.time() * 1000)},
                provenance={},
                policy={}
            ),
            Atom(
                id="huge",
                modality="text",
                content="x" * 1000,
                schema="test.schema@v1",
                facets={"domain": "test", "ts": int(time.time() * 1000) + 1, "trust": 0.9},
                provenance={},
                policy={}
            ),
        ]

        budget = Budget(tokens=50, ms=1000, calls=2)
        goal = {"name": "test-goal", "domain": "test"}

        # Force the "huge" atom to appear cheap.
        def estimator(atom: Atom) -> int:
            return 1 if atom.id == "huge" else 100

        window = pack_context(atoms, budget, goal, token_estimator=estimator)

        assert [atom.id for atom in window.atoms] == ["huge"]


    def test_custom_token_estimator_failure_surfaces(self):
        """Test estimator errors are surfaced clearly."""
        atoms = [
            Atom(
                id="boom",
                modality="text",
                content="x",
                schema="test.schema@v1",
                facets={"domain": "test", "ts": int(time.time() * 1000)},
                provenance={},
                policy={}
            )
        ]

        budget = Budget(tokens=10, ms=1000, calls=1)
        goal = {"name": "test-goal", "domain": "test"}

        def estimator(_atom: Atom) -> int:
            raise RuntimeError("estimator failed")

        try:
            pack_context(atoms, budget, goal, token_estimator=estimator)
            assert False, "expected ValueError"
        except ValueError as exc:
            assert "token_estimator failed" in str(exc)
