from __future__ import annotations

from backend.app.local_ai.providers.base import CanonicalProvider


class ProviderRegistryError(ValueError):
    pass


class ProviderNotRegisteredError(ProviderRegistryError):
    """No adapter is registered for this provider_id.

    `str(exc) == "provider_not_registered"`, matching `ProviderErrorCode.NOT_REGISTERED`
    -- the two are the same failure viewed from the registry side and the
    transport side, and existing `except ValueError` call sites (routes,
    `LocalAIService`) keep working unchanged since this subclasses `ValueError`.
    """

    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id
        super().__init__("provider_not_registered")


class DuplicateProviderError(ProviderRegistryError):
    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id
        super().__init__("duplicate_provider_id")


class ProviderRegistry:
    """Deterministic, explicit provider lookup.

    No plugin discovery, no hidden globals -- callers construct one
    explicitly (`LocalAIService` builds its own from configuration; tests
    build their own scoped instance) and register adapters by
    `provider_id`. Resolution never falls back to a different provider: a
    missing, unavailable, or unsupported provider is always an explicit
    `ProviderNotRegisteredError`, never a silent substitution.
    """

    def __init__(self) -> None:
        self._providers: dict[str, CanonicalProvider] = {}

    def register(self, provider: CanonicalProvider) -> None:
        if provider.provider_id in self._providers:
            raise DuplicateProviderError(provider.provider_id)
        self._providers[provider.provider_id] = provider

    def get(self, provider_id: str) -> CanonicalProvider:
        try:
            return self._providers[provider_id]
        except KeyError:
            raise ProviderNotRegisteredError(provider_id) from None

    def get_or_none(self, provider_id: str) -> CanonicalProvider | None:
        return self._providers.get(provider_id)

    def list_provider_ids(self) -> tuple[str, ...]:
        """Deterministic (registration-order) listing."""
        return tuple(self._providers.keys())

    def resolve_for_model(self, model_profile) -> CanonicalProvider:
        """`ModelProfile.provider_id -> registry lookup -> canonical provider instance`.

        No fallback selection: an unresolvable `provider_id` always raises.
        """
        return self.get(model_profile.provider_id)


__all__ = [
    "DuplicateProviderError",
    "ProviderNotRegisteredError",
    "ProviderRegistry",
    "ProviderRegistryError",
]
