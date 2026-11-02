"""Activities are a private, explicit reviewer source, including Automatic mode."""
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from .exam_forms import PaperImportForm
from .models import Document, ExamPaper


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class TextbookWorkspaceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('textbook-reader')
        self.other = get_user_model().objects.create_user('another-reader')
        self.client.force_login(self.user)
        self.doc = Document.objects.create(owner=self.user, title='Stability textbook', file='book.pdf', status='ready', pages=[
            {'page': 1, 'text': 'Chapter 1: Buoyancy\nBuoyancy supports a floating ship.'},
            {'page': 2, 'text': 'Review Questions\n1. What supports a floating vessel?\nA. Buoyancy\nB. Drag\nC. Thrust\nD. Friction\nAnswer: A\n2. Explain why a ship floats.'},
        ])

    def data(self, **changes):
        values = {'document': self.doc.pk, 'kind': 'reviewer', 'source_mode': 'activities',
                  'title': 'Chapter questions', 'topic': 'Buoyancy', 'question_count': '20'}
        values.update(changes)
        return values

    @patch('learning.reviewers.generate_reviewer.delay')
    @patch('learning.exam_views.import_document')
    def test_automatic_mode_respects_explicit_activities_and_caps_count(self, imported, dispatch):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post('/papers/import/', self.data(kind='auto'))
        self.assertEqual(response.status_code, 302)
        paper = ExamPaper.objects.get(owner=self.user)
        self.assertEqual(paper.generation_source, 'activities')
        self.assertEqual(paper.requested_count, 2)
        imported.assert_not_called()
        dispatch.assert_called_once()
        detail = self.client.get(response.url)
        self.assertContains(detail, 'TEXTBOOK QUESTIONS')

    def test_no_activity_scope_reports_error_without_creating_questions(self):
        with patch('learning.reviewers.generate_reviewer.delay') as dispatch:
            response = self.client.post('/papers/import/', self.data(page_start='1', page_end='1'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'activity', html=False)
        self.assertFalse(ExamPaper.objects.exists())
        dispatch.assert_not_called()

    def test_private_source_metadata_reports_count_but_never_question_text(self):
        response = self.client.get(f'/papers/source/{self.doc.pk}/')
        self.assertEqual(response.json()['activity_count'], 2)
        self.assertNotIn('What supports', response.content.decode())
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/papers/source/{self.doc.pk}/').status_code, 404)

    def test_default_notes_mode_remains_compatible_and_bad_choice_is_rejected(self):
        data = self.data(source_mode='', page_start='1', page_end='1')
        form = PaperImportForm(data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['source_mode'], 'notes')
        self.assertFalse(PaperImportForm(self.data(source_mode='unknown'), user=self.user).is_valid())

    def test_existing_exam_import_does_not_require_activities(self):
        form = PaperImportForm(self.data(kind='exam', page_start='999', source_mode='activities'), user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['source_mode'], 'notes')

    def test_retry_of_deleted_activity_source_does_not_fail_or_queue(self):
        paper = ExamPaper.objects.create(owner=self.user, document=self.doc, kind='reviewer',
            generation_source='activities', generation_status='failed', requested_count=2,
            title='Questions', topic='Ships')
        self.doc.delete()
        with patch('learning.reviewers.generate_reviewer.delay') as dispatch:
            response = self.client.post(f'/papers/{paper.pk}/retry-generation/', {'question_count': 2})
        self.assertEqual(response.status_code, 302)
        self.assertContains(self.client.get(response.url), 'source document is no longer ready')
        dispatch.assert_not_called()
