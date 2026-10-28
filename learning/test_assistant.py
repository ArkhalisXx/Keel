import io
import json
import os
import urllib.error
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import path

from .assistant import AIError, answer_question
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
        self.post(mode='study', documents=[self.doc.pk], question='Explain buoyancy')
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
        with patch('learning.assistant_views.answer_question') as answer:
            self.assertEqual(self.post(mode='web', question='Hello').status_code, 400)
            answer.assert_not_called()


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
    def call(self, provider, response, mode='study', **kwargs):
        env = {'AI_PROVIDER': provider, 'AI_MODEL': '', 'GEMINI_API_KEY': 'private-test-key', 'OPENAI_API_KEY': 'private-test-key'}
        with patch.dict(os.environ, env), patch('urllib.request.urlopen') as request:
            request.return_value.__enter__.return_value = io.BytesIO(json.dumps(response).encode())
            result = answer_question('Explain buoyancy', mode=mode, **kwargs)
            payload = json.loads(request.call_args.args[0].data)
        return result, payload

    def test_study_and_document_requests_never_enable_search_tools(self):
        for provider in ('gemini', 'openai'):
            response = ({'candidates': [{'content': {'parts': [{'text': 'Answer'}]}}]}
                        if provider == 'gemini' else {'output': [{'content': [{'type': 'output_text', 'text': 'Answer'}]}]})
            source = {'id': 1, 'title': 'Notes', 'page': 1, 'text': 'PRIVATE PASSAGE'}
            for mode in ('study', 'documents'):
                result, payload = self.call(provider, response, mode=mode, document_sources=[source])
                self.assertEqual(result['answer'], 'Answer')
                self.assertNotIn('tools', payload)
                self.assertNotIn('tool_choice', payload)
                self.assertNotIn('include', payload)
                self.assertEqual('PRIVATE PASSAGE' in json.dumps(payload), mode == 'documents')
                self.assertNotIn('web_sources', result)
                self.assertNotIn('search_suggestions_html', result)

    def test_removed_web_mode_is_rejected_before_any_network_request(self):
        with patch('urllib.request.urlopen') as request:
            with self.assertRaises(AIError) as error:
                answer_question('Search the web', mode='web')
            self.assertEqual(error.exception.code, 'invalid_mode')
            request.assert_not_called()

    def test_provider_http_errors_never_expose_raw_body_or_key(self):
        for status in (400, 401, 403, 404, 429, 500):
            with self.subTest(status=status), patch.dict(os.environ, {'AI_PROVIDER': 'gemini', 'GEMINI_API_KEY': 'PRIVATE-KEY'}), patch('urllib.request.urlopen') as request:
                request.side_effect = urllib.error.HTTPError('https://api.example', status, 'RAW-SECRET', {}, io.BytesIO(b'PRIVATE-KEY'))
                with self.assertRaises(AIError) as error:
                    answer_question('Hello')
                self.assertEqual(error.exception.code, f'http_{status}')
                self.assertNotIn('PRIVATE-KEY', str(error.exception))
                self.assertNotIn('RAW-SECRET', str(error.exception))

    def test_malformed_provider_responses_raise_safe_format_error(self):
        for data in (None, [], {'candidates': 'not-an-array'}, {'candidates': [None]},
                     {'candidates': [{'content': {'parts': [{'text': 12}]}}]}):
            with self.subTest(data=data), self.assertRaises(AIError) as error:
                self.call('gemini', data)
            self.assertEqual(error.exception.code, 'response_format')
