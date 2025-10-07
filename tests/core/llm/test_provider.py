import json
from types import SimpleNamespace

import pytest

from core.llm.provider import RegionBackedProvider
from topology.registry import clear_registry, register_region
from topology.types import Atom


class DummyRegion:
    key = "dummy.region"
    kind = "model"

    def __init__(self, payload):
        self.payload = payload

    def infer(self, prompt, window, budget):
        yield Atom(
            id="atom-1",
            modality="text" if isinstance(self.payload, str) else "json",
            content=self.payload,
            schema=None,
            facets={"source": self.key, "trust": 0.8},
            provenance={"parents": []},
            policy={},
        )

    # Unused interface methods for the Region protocol
    def read(self, query):  # pragma: no cover - unused in these tests
        return []

    def write(self, atoms):  # pragma: no cover - unused in these tests
        return None

    def validate(self, atoms):  # pragma: no cover - unused in these tests
        return {"ok": True, "findings": []}

    def summarize(self, atoms, goal):  # pragma: no cover - unused in these tests
        raise NotImplementedError

    def reconcile(self, left, right, goal):  # pragma: no cover - unused in these tests
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _clear_registry():
    clear_registry()
    yield
    clear_registry()


def test_region_backed_provider_returns_text(monkeypatch):
    region = DummyRegion("hello world")
    register_region(region)
    provider = RegionBackedProvider(region_key=region.key)

    result = provider.generate(prompt="hi", model="test-model")

    assert result == "hello world"


def test_region_backed_provider_serialises_dict(monkeypatch):
    payload = {"tests": ["one", "two"]}
    region = DummyRegion(payload)
    register_region(region)
    provider = RegionBackedProvider(region_key=region.key)

    result = provider.generate(prompt="hi", model="test-model")

    assert json.loads(result) == payload


def test_region_backed_provider_missing_region():
    provider = RegionBackedProvider(region_key="missing")

    with pytest.raises(RuntimeError, match="not registered"):
        provider.generate(prompt="hi", model="any")


def test_region_backed_provider_no_atoms():
    class EmptyRegion(DummyRegion):
        def infer(self, prompt, window, budget):
            return []

    region = EmptyRegion(None)
    register_region(region)

    provider = RegionBackedProvider(region_key=region.key)

    with pytest.raises(RuntimeError, match="returned no atoms"):
        provider.generate(prompt="hi", model="any")
