import pytest
from pydantic import ValidationError

from backend.app.project_retrieval.configuration import RetrievalProviderConfiguration
from backend.app.project_retrieval.provider_registry import ProviderDevice


def test_canonical_provider_defaults_and_device_policy() -> None:
    configuration = RetrievalProviderConfiguration()
    assert configuration.embedding_model == "BAAI/bge-small-en-v1.5"
    assert (
        configuration.reranker_model
        == "cross-encoder/ms-marco-MiniLM-L6-v2"
    )
    assert configuration.embedding_device == ProviderDevice.AUTO
    assert configuration.reranker_device == ProviderDevice.AUTO
    assert configuration.local_files_only is True


def test_configuration_rejects_arbitrary_models_devices_and_batches() -> None:
    with pytest.raises(ValidationError):
        RetrievalProviderConfiguration(embedding_model="attacker/model")
    with pytest.raises(ValidationError):
        RetrievalProviderConfiguration(embedding_device="cuda:7")
    with pytest.raises(ValidationError):
        RetrievalProviderConfiguration(reranker_batch_size=21)
    with pytest.raises(ValidationError):
        RetrievalProviderConfiguration(local_files_only=False)
