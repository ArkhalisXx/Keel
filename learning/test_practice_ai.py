from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import path

from .assistant import AIError
from .models import Document, PracticeRun
from .practice_ai import practice_card_help

urlpatterns = [path('runs/<int:pk>/help/', practice_card_help)]


@override_settings(ROOT_URLCONF=__name__)
class FlashcardAITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('learner')
        self.other = User.objects.create_user('other')
        self.client.force_login(self.user)
        self.doc = Document.objects.create(owner=self.user, title='Hydrostatics', file='notes.txt', status='ready',
            pages=[{'page': 1, 'text': 'Buoyancy is the upward force on an immersed hull.'}])
        self.question = {'id': 7, 'prompt': 'What supports a floating hull?',
            'choices': ['Buoyancy', 'Drag', 'Thrust', 'Friction'], 'correct_index': 0,
            'explanation': 'SECRET SAVED EXPLANATION', 'reference': 'SAVED KEY REFERENCE',
            'source_document_id': self.doc.pk}
        self.run = PracticeRun.objects.create(owner=self.user, title='Set A', questions=[self.question])

    def post(self, **data):
        return self.client.post(f'/runs/{self.run.pk}/help/', data)

    @staticmethod
    def reply():
        return {'answer': 'Consider the upward force from the surrounding water.',
                'document_sources': [], 'web_sources': [], 'mode': 'documents', 'warning': ''}

    def test_login_csrf_and_other_user_access(self):
        self.client.logout()
        self.assertEqual(self.post(card=0).status_code, 302)
        self.client.force_login(self.other)
        self.assertEqual(self.post(card=0).status_code, 404)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(f'/runs/{self.run.pk}/help/', {'card': 0}).status_code, 403)
        self.assertEqual(self.client.get(f'/runs/{self.run.pk}/help/').status_code, 405)

    @patch('learning.practice_ai.answer_question')
    def test_unanswered_explain_is_only_a_hint_without_saved_key(self, answer):
        answer.return_value = self.reply()
        response = self.post(card=0, intent='explain')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['hint'])
        prompt = answer.call_args.args[0]
        self.assertIn('Do not name the correct letter', prompt)
        self.assertNotIn('SECRET SAVED EXPLANATION', prompt)
        self.assertNotIn('SAVED KEY REFERENCE', prompt)
        self.assertNotIn('saved practice answer is', prompt)
        self.assertEqual(answer.call_args.kwargs['mode'], 'documents')
        self.assertEqual(answer.call_args.kwargs['document_sources'][0]['id'], self.doc.pk)
        self.assertEqual(self.run.results, [])

    @patch('learning.practice_ai.answer_question')
    def test_answered_card_can_explain_selected_and_saved_answer(self, answer):
        answer.return_value = self.reply()
        self.run.results = [{'id': 7, 'selected_index': 1, 'correct': False}]
        self.run.save(update_fields=['results'])
        response = self.post(card=0, intent='explain')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['hint'])
        prompt = answer.call_args.args[0]
        self.assertIn('submitted an answer: B', prompt)
        self.assertIn('saved practice answer is A', prompt)
        self.assertIn('SECRET SAVED EXPLANATION', prompt)
        self.assertIn('do not call it independently verified', prompt)

    @patch('learning.practice_ai.answer_question')
    def test_private_or_not_ready_documents_are_not_retrieved(self, answer):
        answer.return_value = self.reply()
        for owner, status in ((self.other, 'ready'), (self.user, 'processing')):
            with self.subTest(owner=owner.pk, status=status):
                Document.objects.filter(pk=self.doc.pk).update(owner=owner, status=status)
                response = self.post(card=0, intent='hint')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(answer.call_args.kwargs['mode'], 'study')
                self.assertEqual(answer.call_args.kwargs['document_sources'], [])
                self.assertIn('general study guidance', response.json()['warning'])

    @patch('learning.practice_ai.answer_question')
    def test_invalid_card_intent_and_json_never_call_provider(self, answer):
        for card in ('no', '-1', '1', '', '0.1'):
            self.assertEqual(self.post(card=card).status_code, 400)
        self.assertEqual(self.post(card=0, intent='reveal_key').status_code, 400)
        self.assertEqual(self.client.post(f'/runs/{self.run.pk}/help/', data='[]', content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post(f'/runs/{self.run.pk}/help/', data='{"card": true}', content_type='application/json').status_code, 400)
        answer.assert_not_called()

    @patch('learning.practice_ai.answer_question', side_effect=AIError('Add your provider key privately.', 'missing_key'))
    def test_missing_key_has_safe_error_and_matching_passages(self, answer):
        response = self.post(card=0)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['code'], 'missing_key')
        self.assertEqual(response.json()['document_sources'][0]['id'], self.doc.pk)

    @patch('learning.practice_ai.answer_question')
    def test_printed_answer_key_not_sent_in_hint_excerpts(self, answer):
        answer.return_value = self.reply()
        self.doc.pages = [{'page': 1, 'text': 'Buoyancy supports the hull.\nAnswer Key\n1 A'}]
        self.doc.save(update_fields=['pages'])
        self.post(card=0)
        self.assertEqual(answer.call_args.kwargs['document_sources'], [])
        self.assertEqual(answer.call_args.kwargs['mode'], 'study')

    @patch('learning.practice_ai.answer_question')
    def test_future_card_is_not_available_for_hints_yet(self, answer):
        self.run.questions.append(dict(self.question, id=8))
        self.run.save(update_fields=['questions'])
        self.assertEqual(self.post(card=1, intent='hint').status_code, 400)
        answer.assert_not_called()
