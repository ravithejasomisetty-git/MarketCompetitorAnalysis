"""Verify retry boundaries and provider switching without paid inference calls."""
import unittest
from unittest.mock import Mock, patch
import httpx
from openai import APIConnectionError, APITimeoutError, APIStatusError
from pydantic import BaseModel, ValidationError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from market_analysis.model_router import ModelRouter, create_model_router
from market_analysis.research_errors import ResearchFailure


def status_error(status, code=None):
    response = httpx.Response(status, request=httpx.Request('POST', 'https://example.com'))
    return APIStatusError('private-provider-body', response=response, body={'code': code} if code else {})


class Decision(BaseModel):
    allowed: bool


class ModelRoutingTests(unittest.TestCase):
    def setUp(self):
        self.primary, self.backup = Mock(), Mock()
        self.router = ModelRouter(self.primary, 'primary-model', self.backup, 'backup-model')
        self.messages = [SystemMessage(content='fixed rules'), HumanMessage(content='private brief'),
            AIMessage(content='', tool_calls=[{'id': 'prior-tool', 'name': 'search', 'args': {}}]),
            ToolMessage(content='already retrieved evidence', tool_call_id='prior-tool')]
        pause = patch('market_analysis.model_router.sleep')
        pause.start();self.addCleanup(pause.stop)

    def test_success_never_contacts_backup(self):
        answer = AIMessage(content='success')
        self.primary.invoke.return_value = answer
        self.assertIs(self.router.invoke(self.messages), answer)
        self.backup.invoke.assert_not_called()

    def test_recovers_on_primary_retry(self):
        self.primary.invoke.side_effect = [status_error(503), AIMessage(content='recovered')]
        self.assertEqual(self.router.invoke(self.messages).content, 'recovered')
        self.assertEqual(self.primary.invoke.call_count, 2)
        self.backup.invoke.assert_not_called()

    def test_availability_errors_use_same_conversation_and_config(self):
        errors = [status_error(408), status_error(429), status_error(500), status_error(502), status_error(503),
            status_error(504), APITimeoutError(request=httpx.Request('POST', 'https://example.com')),
            APIConnectionError(request=httpx.Request('POST', 'https://example.com'))]
        for error in errors:
            with self.subTest(error=type(error).__name__, status=getattr(error,'status_code',None)):
                self.primary.reset_mock();self.backup.reset_mock()
                self.primary.invoke.side_effect = error
                self.backup.invoke.return_value = AIMessage(content='backup answer')
                result = self.router.invoke(self.messages, config={'tags': ['request-tag']})
                self.assertEqual(result.content, 'backup answer')
                self.assertEqual(self.primary.invoke.call_count, 2)
                self.backup.invoke.assert_called_once_with(self.messages, config={'tags': ['request-tag']})
                self.assertEqual(len(self.messages), 4)

    def test_auth_billing_policy_context_and_schema_errors_never_switch(self):
        try:
            Decision.model_validate({'allowed': 'invalid'})
        except ValidationError as invalid:
            schema_error = invalid
        errors = [status_error(401), status_error(403), status_error(404), status_error(400),
            status_error(429,'insufficient_quota'), status_error(429,'billing_hard_limit_reached'),
            status_error(500,'content_filter'), schema_error, ValueError('invalid model data')]
        for error in errors:
            with self.subTest(status=getattr(error,'status_code',None), code=getattr(error,'code',None)):
                self.primary.reset_mock();self.backup.reset_mock()
                self.primary.invoke.side_effect = error
                with self.assertRaises(type(error)):
                    self.router.invoke(self.messages)
                self.primary.invoke.assert_called_once()
                self.backup.invoke.assert_not_called()

    def test_valid_safety_decline_is_returned_without_switching(self):
        decision = {'outcome': 'decline', 'reason': 'instruction_override'}
        self.primary.invoke.return_value = decision
        self.assertEqual(self.router.invoke(self.messages), decision)
        self.backup.invoke.assert_not_called()

    def test_both_providers_failing_produces_safe_error(self):
        self.primary.invoke.side_effect = status_error(503)
        self.backup.invoke.side_effect = status_error(404)
        with self.assertRaises(ResearchFailure) as failure:
            self.router.invoke(self.messages)
        self.assertEqual(failure.exception.code, 'fallback_model_failed')
        self.assertNotIn('private-provider-body',failure.exception.public_message)
        self.assertEqual(self.primary.invoke.call_count, 2)
        self.backup.invoke.assert_called_once()

    def test_tool_bindings_and_named_required_tool_are_preserved(self):
        tool = {'type':'function','function':{'name':'search','description':'Search evidence','parameters':{'type':'object','properties':{}}}}
        bound = self.router.bind_tools([tool], parallel_tool_calls=False)
        self.primary.bind_tools.assert_called_once_with([tool],parallel_tool_calls=False)
        self.backup.bind_tools.assert_called_once_with([tool])
        self.primary.bind_tools.return_value.invoke.side_effect = status_error(503)
        answer=AIMessage(content='',tool_calls=[{'id':'new','name':'search','args':{}}])
        self.backup.bind_tools.return_value.invoke.return_value=answer
        self.assertIs(bound.invoke(self.messages,tool_choice='required'),answer)
        self.assertEqual(self.backup.bind_tools.return_value.invoke.call_args.kwargs['tool_choice'],
            {'type':'function','function':{'name':'search'}})

    def test_multiple_required_tools_enforced_without_mutating_history(self):
        tool=lambda name:{'type':'function','function':{'name':name,'parameters':{'type':'object','properties':{}}}}
        bound=self.router.bind_tools([tool('search'),tool('fetch')])
        self.primary.bind_tools.return_value.invoke.side_effect=status_error(503)
        self.backup.bind_tools.return_value.invoke.return_value=AIMessage(content='no tool selected')
        with self.assertRaises(ResearchFailure):
            bound.invoke(self.messages,tool_choice='required')
        self.assertEqual(len(self.messages),4)
        self.assertEqual(self.backup.bind_tools.return_value.invoke.call_args.kwargs['tool_choice'],'auto')

    def test_structured_calls_receive_same_schema_on_both_providers(self):
        structured=self.router.with_structured_output(Decision)
        self.primary.with_structured_output.assert_called_once_with(Decision)
        self.backup.with_structured_output.assert_called_once_with(Decision,method='json_schema',strict=False)
        self.primary.with_structured_output.return_value.invoke.side_effect=status_error(503)
        self.backup.with_structured_output.return_value.invoke.return_value=Decision(allowed=True)
        self.assertTrue(structured.invoke(self.messages).allowed)


class RouterConfigurationTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict('os.environ',{'OPENAI_MODEL':'primary-model','MODEL_ROUTING_ENABLED':'true',
            'NEBIUS_API_KEY':'private-nebius-key','NEBIUS_BASE_URL':'https://api.tokenfactory.nebius.com/v1/',
            'NEBIUS_MODEL':'vendor/configured-model'})
        env.start();self.addCleanup(env.stop)

    def test_clients_use_separate_keys_endpoints_and_no_sdk_retries(self):
        factory=Mock()
        router=create_model_router('private-openai-key',factory)
        first,second=[c.kwargs for c in factory.call_args_list]
        self.assertEqual(first['model'],'primary-model')
        self.assertEqual(first['api_key'],'private-openai-key')
        self.assertNotIn('base_url',first)
        self.assertEqual(second['api_key'],'private-nebius-key')
        self.assertEqual(second['base_url'],'https://api.tokenfactory.nebius.com/v1')
        self.assertEqual(second['model'],'vendor/configured-model')
        self.assertEqual(first['max_retries'],0)
        self.assertEqual(second['max_retries'],0)
        self.assertIsNotNone(router.fallback)

    def test_disabled_router_does_not_require_nebius(self):
        with patch.dict('os.environ',{'MODEL_ROUTING_ENABLED':'false','NEBIUS_API_KEY':''}):
            factory=Mock()
            router=create_model_router('key',factory)
            self.assertIsNone(router.fallback)
            factory.assert_called_once()

    def test_incomplete_configuration_is_rejected_before_clients_exist(self):
        for field in ['NEBIUS_API_KEY','NEBIUS_MODEL','NEBIUS_BASE_URL']:
            with self.subTest(field=field),patch.dict('os.environ',{field:''}):
                factory=Mock()
                with self.assertRaises(ResearchFailure):create_model_router('key',factory)
                factory.assert_not_called()

    def test_keys_are_never_sent_to_untrusted_hosts(self):
        for url in ['http://api.tokenfactory.nebius.com/v1','https://evil.example/v1',
                    'https://key@api.tokenfactory.nebius.com/v1','https://api.tokenfactory.nebius.com/v1?token=secret']:
            with self.subTest(url=url),patch.dict('os.environ',{'NEBIUS_BASE_URL':url}):
                factory=Mock()
                with self.assertRaises(ResearchFailure):create_model_router('key',factory)
                factory.assert_not_called()
