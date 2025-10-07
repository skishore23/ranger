from __future__ import annotations

from dataclasses import dataclass

from core.llm.provider import (
    RegionBackedProvider,
    clear_llm_profiles,
    register_llm_profile,
    resolve_llm_profile,
)
from topology.registry import clear_registry, register_region, get_region


@dataclass
class DummyRegion:
    key: str = "test.region"
    kind: str = "model"

    def read(self, query):
        return []

    def write(self, atoms):
        return None

    def validate(self, atoms):
        return {}

    def infer(self, prompt, window, budget=None):
        return []

    def act(self, window):
        return ([], [])

    def summarize(self, atoms, goal):
        return atoms[0] if atoms else None

    def reconcile(self, left, right, goal):
        return True, left, None


def setup_module(_module):
    clear_registry()
    clear_llm_profiles()


def teardown_module(_module):
    clear_registry()
    clear_llm_profiles()


def test_register_llm_profile_with_provider_instance():
    class StaticProvider:
        def generate(self, **kwargs):
            return "ok"

    register_llm_profile(
        "static.profile",
        provider=StaticProvider(),
        defaults={"model": "stub-model"},
    )

    provider, defaults = resolve_llm_profile("static.profile")
    assert defaults["model"] == "stub-model"
    assert provider.generate(prompt="", model="stub-model") == "ok"


def test_register_llm_profile_with_region_factory():
    def ensure_region():
        register_region(DummyRegion())

    register_llm_profile(
        "region.profile",
        region_key="test.region",
        defaults={"model": "stub-model"},
        region_factory=ensure_region,
    )

    assert get_region("test.region") is None
    provider, defaults = resolve_llm_profile("region.profile")
    assert isinstance(provider, RegionBackedProvider)
    assert defaults["model"] == "stub-model"
    # Region is registered lazily by resolve
    assert get_region("test.region") is not None
