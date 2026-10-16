"""Observable SDK behavior that must survive removal of duplicate setup logic."""
import pytest

from core.sdk import Agent, llm, step, tool


@pytest.mark.parametrize('decorator, expected', [(step, {'custom', 'compute'}), (tool, {'custom', 'action'})])
def test_decorator_captures_tags_when_factory_is_called(decorator, expected):
    tags = {'custom'}
    decorate = decorator(tags=tags)
    tags.add('later')
    capability = decorate(lambda state: {})
    assert capability.tags == expected
    tags.clear()
    assert capability.tags == expected


@pytest.mark.parametrize('schema, response, expected', [
    (None, 'plain text', 'plain text'),
    ({'type': 'object', 'properties': {'answer': {'type': 'integer'}}}, '{"answer": 7}', {'answer': 7}),
])
def test_llm_defaults_and_zero_temperature_reach_provider(schema, response, expected):
    class Provider:
        def generate(self, **request):
            assert request['model'] == 'gpt-4o-mini'
            assert request['system'] is None
            assert request['temperature'] == 0.0
            assert request['max_tokens'] == 10
            return response

    @llm(provider=Provider(), model='', system='', temperature=0.0, max_tokens=10,
         outputs=['result'], schema=schema)
    def generate(state):
        pass

    result = Agent([generate], trace=False).run(initial={}, goal=lambda state: state.exists('result'))
    assert result.ok and result.final['result'] == expected
