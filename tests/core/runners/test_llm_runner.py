import pytest
from unittest.mock import Mock, patch
from core.runners.llm_runner import LLMRunner
from core.workspace import Snapshot
from core.capability import Capability


@pytest.fixture
def mock_provider():
    return Mock()


@pytest.fixture
def llm_runner(mock_provider):
    return LLMRunner(provider=mock_provider, model='gpt-4o-mini')


def test_run_with_template(llm_runner, mock_provider):
    # Arrange
    cap = Mock(spec=Capability)
    cap.reads = {'input'}
    cap.writes = {'output'}
    snap = Mock(spec=Snapshot)
    snap.get.return_value = 'test input'
    llm_runner.template = 'Processed: {{ input }}'
    llm_runner.schema = {'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value']}
    mock_provider.generate.return_value = '{"value": "test output"}'

    # Act
    result = llm_runner.run(cap, snap)

    # Assert
    assert result == {'output': {'value': 'test output'}}
    mock_provider.generate.assert_called_once()


def test_run_without_schema(llm_runner, mock_provider):
    # Arrange
    cap = Mock(spec=Capability)
    cap.reads = {'input'}
    cap.writes = {'output'}
    snap = Mock(spec=Snapshot)
    snap.get.return_value = 'test input'
    llm_runner.schema = None
    mock_provider.generate.return_value = 'simple response'

    # Act
    result = llm_runner.run(cap, snap)

    # Assert
    assert result == {'output': 'simple response'}
    mock_provider.generate.assert_called_once()


def test_run_with_invalid_json(llm_runner, mock_provider):
    # Arrange
    cap = Mock(spec=Capability)
    cap.reads = {'input'}
    cap.writes = {'output'}
    snap = Mock(spec=Snapshot)
    snap.get.return_value = 'test input'
    llm_runner.schema = {'type': 'object', 'properties': {'output': {'type': 'string'}}}
    mock_provider.generate.return_value = 'invalid json'

    # Act & Assert
    with pytest.raises(RuntimeError, match='LLM returned invalid JSON'):
        llm_runner.run(cap, snap)
