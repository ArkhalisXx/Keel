import io
import json
import os
import urllib.error
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import path

from .assistant import AIError, answer_question, safe_source_url
from .assistant_views import assistant_chat
from .models import Document

# Isolate these tests from the parent agent's URL integration work.
urlpatterns = [path('chat/', assistant_chat)]


@override_settings(ROOT_URLCONF=__name__)
class AssistantViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('reader')
        self.other = User.objects.create_user('other')
        self.client.force_login(self.user)
        self.doc = Document.objects.create(owner=self.user, title='Stability notes',
            file='test.txt', status=Document.Status.READY,
            pages=[{'page': 2, 'text': 'Metacentric height measures initial stability.', 'source': 'text'}])

    def post(self, **data):
        return self.client.post('/chat/', data=json.dumps(data), content_type='application/json')

    def test_login_and_csrf_required(self):
        self.client.logout()
        self.assertEqual(self.post(mode='study', question='Hello').status_code, 302)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post('/chat/', {'mode': 'study', 'question': 'Hello'}).status_code, 403)

    def test_document_ownership_and_processing_status_enforced(self):
        private = Document.objects.create(owner=self.other, title='Private', file='other.txt', status='ready')
        queued = Document.objects.create(owner=self.user, title='Queued', file='new.txt')
        with patch('learning.assistant_views.answer_question') as answer:
            for doc in (private, queued):
                response = self.post(mode='documents', documents=[doc.pk], question='Explain stability')
                self.assertEqual(response.status_code, 400)
            answer.assert_not_called()
        self.assertEqual(self.post(mode='documents', documents=[], question='Explain stability').status_code, 400)

    @patch('learning.assistant_views.answer_question')
    def test_context_isolation_and_bounded_history(self, answer):
        answer.return_value = {'answer': 'An explanation', 'document_sources': [], 'web_sources': [],
                               'mode': 'documents', 'web_used': False, 'warning': ''}
        for _ in range(5):
            response = self.post(mode='documents', documents=[self.doc.pk], question='Explain metacentric height')
            self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.client.session['assistant_history']), 6)
        self.assertEqual(answer.call_args.kwargs['document_sources'][0]['id'], self.doc.pk)
        self.post(mode='web', documents=[self.doc.pk], question='Find PRC requirements')
        self.assertEqual(answer.call_args.kwargs['history'], [])
        self.assertEqual(answer.call_args.kwargs['document_sources'], [])
        self.post(reset=True)
        self.assertNotIn('assistant_history', self.client.session)

    @patch('learning.assistant_views.answer_question', side_effect=AIError('No key', 'missing_key'))
    def test_document_sources_work_without_ai_key(self, answer):
        response = self.post(mode='documents', documents=[self.doc.pk], question='Explain metacentric height')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['configured'])
        self.assertEqual(response.json()['document_sources'][0]['page'], 2)

    def test_no_matching_passages_do_not_invent_document_answer(self):
        with patch('learning.assistant_views.answer_question') as answer:
            response = self.post(mode='documents', documents=[self.doc.pk], question='banana plantation')
            self.assertIn('No matching passages', response.json()['answer'])
            answer.assert_not_called()

    def test_invalid_json_and_mode(self):
        self.assertEqual(self.client.post('/chat/', data='[]', content_type='application/json').status_code, 400)
        self.assertEqual(self.post(mode='unknown', question='Hello').status_code, 400)


    def test_real_missing_key_document_fallback_returns_local_sources(self):
        with patch.dict(os.environ, {'AI_PROVIDER': 'gemini', 'GEMINI_API_KEY': ''}), patch('urllib.request.urlopen') as request:
            response = self.post(mode='documents', documents=[self.doc.pk], question='Explain metacentric height')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['configured'])
        self.assertEqual(response.json()['document_sources'][0]['page'], 2)
        self.assertIn('matching passages', response.json()['answer'])
        request.assert_not_called()

    @patch('learning.assistant_views.answer_question')
    def test_changing_document_selection_drops_old_conversation(self, answer):
        answer.return_value = {'answer': 'An explanation', 'document_sources': [], 'web_sources': [],
                               'mode': 'documents', 'web_used': False, 'warning': ''}
        other_doc = Document.objects.create(owner=self.user, title='New notes', file='new.txt',
            status=Document.Status.READY, pages=[{'page': 1, 'text': 'Stability is measured by metacentric height.'}])
        self.post(mode='documents', documents=[self.doc.pk], question='Explain metacentric height')
        self.post(mode='documents', documents=[other_doc.pk], question='Explain metacentric height')
        self.assertEqual(answer.call_args.kwargs['history'], [])


class AssistantProviderTests(TestCase):
    def call(self, provider, response, mode='web', **kwargs):
        env = {'AI_PROVIDER': provider, 'AI_MODEL': '', 'AI_WEB_MODEL': '', 'GEMINI_API_KEY': 'private-test-key', 'OPENAI_API_KEY': 'private-test-key'}
        with patch.dict(os.environ, env), patch('urllib.request.urlopen') as request:
            request.return_value.__enter__.return_value = io.BytesIO(json.dumps(response).encode())
            result = answer_question('Explain buoyancy', mode=mode, **kwargs)
            payload = json.loads(request.call_args.args[0].data)
        return result, payload

    def test_web_model_override_is_validated_and_only_applies_to_web(self):
        for provider in ('gemini', 'openai'):
            for mode in ('web', 'study', 'documents'):
                response = ({'candidates': [{'content': {'parts': [{'text': 'Answer'}]}}]}
                            if provider == 'gemini' else {'output': [{'content': [{'type': 'output_text', 'text': 'Answer'}]}]})
                with self.subTest(provider=provider, mode=mode), patch.dict(os.environ, {
                    'AI_PROVIDER': provider, 'AI_MODEL': 'standard-model', 'AI_WEB_MODEL': 'fast-search-model',
                    'GEMINI_API_KEY': 'test-key', 'OPENAI_API_KEY': 'test-key',
                }), patch('learning.assistant._request', return_value=response) as request:
                    answer_question('What is a stern?', mode=mode)
                    expected = 'fast-search-model' if mode == 'web' else 'standard-model'
                    if provider == 'gemini':
                        self.assertIn(f'/models/{expected}:', request.call_args.args[0])
                    else:
                        self.assertEqual(request.call_args.args[2]['model'], expected)
        with patch.dict(os.environ, {'AI_PROVIDER': 'gemini', 'GEMINI_API_KEY': 'test', 'AI_WEB_MODEL': 'https://bad/model'}), patch('learning.assistant._request') as request:
            with self.assertRaises(AIError):
                answer_question('Hello', mode='web')
            request.assert_not_called()

    def test_web_followups_keep_recent_context_within_budget(self):
        history = [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': str(i) * 4000} for i in range(6)]
        for provider in ('gemini', 'openai'):
            response = ({'candidates': [{'content': {'parts': [{'text': 'Answer'}]}}]}
                        if provider == 'gemini' else {'output': [{'content': [{'type': 'output_text', 'text': 'Answer'}]}]})
            _, payload = self.call(provider, response, history=history)
            turns = payload['contents'][:-1] if provider == 'gemini' else payload['input'][:-1]
            texts = [turn['parts'][0]['text'] if provider == 'gemini' else turn['content'] for turn in turns]
            self.assertLessEqual(sum(map(len, texts)), 6000)
            self.assertEqual(texts[-1], history[-1]['content'])
            self.assertNotIn(history[0]['content'], texts)

    def test_gemini_web_grounding_and_private_passage_exclusion(self):
        response = {'candidates': [{'content': {'parts': [{'text': 'Grounded explanation'}]},
            'groundingMetadata': {'groundingChunks': [
                {'web': {'uri': 'https://example.org/reference', 'title': 'Reference'}},
                {'web': {'uri': 'javascript:alert(1)', 'title': 'Bad URL'}}]}}]}
        result, payload = self.call('gemini', response, document_sources=[{'text': 'PRIVATE EXCERPT'}])
        self.assertEqual(payload['tools'], [{'google_search': {}}])
        self.assertNotIn('PRIVATE EXCERPT', json.dumps(payload))
        self.assertTrue(result['web_used'])
        self.assertEqual(result['web_sources'], [{'url': 'https://example.org/reference', 'title': 'Reference'}])

    def test_openai_web_tool_and_citations(self):
        response = {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'An explanation',
            'annotations': [{'type': 'url_citation', 'url': 'https://example.org', 'title': 'Reference'}]}]}]}
        result, payload = self.call('openai', response)
        self.assertEqual(payload['tools'], [{'type': 'web_search'}])
        self.assertEqual(payload['tool_choice'], 'required')
        self.assertTrue(result['web_used'])
        self.assertFalse(payload['store'])

    def test_no_search_sources_do_not_claim_verified_internet_access(self):
        result, payload = self.call('gemini', {'candidates': [{'content': {'parts': [{'text': 'An answer'}]}}]})
        self.assertFalse(result['web_used'])
        self.assertIn('no verifiable web source', result['warning'])
        result, payload = self.call('gemini', {'candidates': [{'content': {'parts': [{'text': 'An answer'}]}}]}, mode='study')
        self.assertNotIn('tools', payload)
        self.assertEqual(result['web_sources'], [])

    def test_provider_http_errors_never_expose_raw_body_or_key(self):
        for status in (400, 401, 403, 404, 429, 500):
            with self.subTest(status=status), patch.dict(os.environ, {'AI_PROVIDER': 'gemini', 'GEMINI_API_KEY': 'PRIVATE-KEY'}), patch('urllib.request.urlopen') as request:
                request.side_effect = urllib.error.HTTPError('https://api.example', status, 'RAW-SECRET', {}, io.BytesIO(b'PRIVATE-KEY'))
                with self.assertRaises(AIError) as error:
                    answer_question('Hello')
                self.assertEqual(error.exception.code, f'http_{status}')
                self.assertNotIn('PRIVATE-KEY', str(error.exception))
                self.assertNotIn('RAW-SECRET', str(error.exception))

    def test_source_url_validation(self):
        for value in ('javascript:alert(1)', 'data:text/html,x', 'file:///etc/passwd', 'https://name:password@example.org', 'https://example.org/\nvalue'):
            self.assertIsNone(safe_source_url(value))
        self.assertEqual(safe_source_url('https://example.org/reference'), 'https://example.org/reference')

    def test_openai_sources_reject_executable_and_credential_urls(self):
        response = {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Answer',
            'annotations': [
                {'type': 'url_citation', 'url': 'javascript:alert(1)', 'title': 'Unsafe'},
                {'type': 'url_citation', 'url': 'https://user:secret@example.org', 'title': 'Credential URL'},
                {'type': 'url_citation', 'url': 'https://example.org/safe', 'title': 'Safe'},
                {'type': 'url_citation', 'url': 'https://example.org/safe', 'title': 'Duplicate'},
            ]}]}]}
        result, payload = self.call('openai', response)
        self.assertEqual(result['web_sources'], [{'url': 'https://example.org/safe', 'title': 'Safe'}])

    def test_google_search_suggestions_only_come_from_grounding_metadata(self):
        response = {'candidates': [{'content': {'parts': [{'text': 'Answer <b>not a search suggestion</b>'}]},
            'groundingMetadata': {'searchEntryPoint': {'renderedContent': '<div>Google search suggestions</div>'}}}]}
        result, payload = self.call('gemini', response)
        self.assertEqual(result['search_suggestions_html'], '<div>Google search suggestions</div>')
        result, payload = self.call('gemini', response, mode='study')
        self.assertEqual(result['search_suggestions_html'], '')
        response['candidates'][0]['groundingMetadata']['searchEntryPoint']['renderedContent'] = 'x' * 60001
        result, payload = self.call('gemini', response)
        self.assertEqual(result['search_suggestions_html'], '')
        response['candidates'][0].pop('groundingMetadata')
        result, payload = self.call('gemini', response)
        self.assertEqual(result['search_suggestions_html'], '')

    def test_malformed_provider_responses_raise_safe_format_error(self):
        malformed = [None, [], {'candidates': 'not-an-array'}, {'candidates': [None]},
            {'candidates': [{'content': {'parts': [{'text': 12}]}}]},
            {'candidates': [{'content': {'parts': [{'text': 'Answer'}]}, 'groundingMetadata': []}]},
            {'candidates': [{'content': {'parts': [{'text': 'Answer'}]},
                'groundingMetadata': {'searchEntryPoint': {'renderedContent': {'bad': 'type'}}}}]}]
        for data in malformed:
            with self.subTest(data=data):
                with self.assertRaises(AIError) as error:
                    self.call('gemini', data)
                self.assertEqual(error.exception.code, 'response_format')
