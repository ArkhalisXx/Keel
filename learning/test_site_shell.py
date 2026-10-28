"""The shared workspace stays consistent without changing private study flows."""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Attempt, Document, ExamPaper, PracticeRun, Question


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class SiteShellTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('shell-reader')
        self.other = User.objects.create_user('shell-other')
        self.client.force_login(self.user)
        self.doc = Document.objects.create(owner=self.user, title='My stability notes', file='notes.txt', status='ready',
            pages=[{'page': 1, 'text': 'Buoyancy supports a floating ship.'}])
        self.paper = ExamPaper.objects.create(owner=self.user, document=self.doc, title='My mock paper', topic='Hydrostatics')
        self.question = Question.objects.create(owner=self.user, paper=self.paper, source_document=self.doc,
            topic='Hydrostatics', prompt='What supports a floating ship?', choices=['Buoyancy','Drag','Thrust','Friction'],
            correct_index=0, explanation='Buoyancy supports a floating ship.', reviewed=True)
        self.attempt = Attempt.objects.create(owner=self.user, question=self.question, selected_index=0, correct=True)
        self.run = PracticeRun.objects.create(owner=self.user, title='My flashcards', questions=[{
            'id': self.question.pk, 'number': '1', 'prompt': self.question.prompt, 'choices': self.question.choices,
            'correct_index': 0, 'explanation': self.question.explanation, 'reference': 'Notes, page 1',
        }])

    def test_every_learning_screen_uses_the_correct_shared_navigation(self):
        pages = [
            ('dashboard', [], 'overview'), ('library', [], 'library'), ('progress', [], 'progress'),
            ('import_paper', [], 'papers'), ('paper_detail', [self.paper.pk], 'papers'),
            ('review_question', [self.question.pk], 'papers'), ('delete_paper', [self.paper.pk], 'papers'),
            ('practice', [], 'practice'), ('question', [self.question.pk], 'practice'),
            ('result', [self.attempt.pk], 'practice'), ('practice_run', [self.run.pk], 'practice'),
            ('assistant', [], 'assistant'), ('tutor', [], 'assistant'),
        ]
        for name, args, active in pages:
            with self.subTest(page=name):
                response = self.client.get(reverse(name, args=args))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['workspace_shell'])
                self.assertEqual(response.context['active_section'], active)
                self.assertContains(response, 'class="dash-sidebar"', count=1)
                self.assertContains(response, 'class="dash-topbar"', count=1)
                self.assertContains(response, 'aria-current="page"', count=1)
                self.assertContains(response, 'id="workspace-content"')
                self.assertContains(response, '/progress/')
                self.assertNotContains(response, 'Study focus')
                self.assertNotContains(response, 'study-focus')

    def test_public_auth_pages_use_public_layout_and_preserve_custom_copy(self):
        self.client.logout()
        for name in ('login', 'register'):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'class="dash-sidebar"')
            self.assertContains(response, 'auth-page')
            self.assertContains(response, 'Keel Believes')

    def test_library_overview_is_owned_and_independent_of_filters(self):
        Document.objects.create(owner=self.user, title='Processing paper', file='scan.pdf', status='processing')
        Document.objects.create(owner=self.other, title='Private textbook', file='secret.pdf', status='ready')
        response = self.client.get(reverse('library'), {'q': 'missing title'})
        self.assertEqual(response.context['library_stats'], {'total': 2, 'ready': 1, 'processing': 1})
        self.assertEqual(len(response.context['documents']), 0)
        self.assertNotContains(response, 'Private textbook')

    def test_upload_still_queues_an_owned_document_after_commit(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory), \
                patch('learning.tasks.process_document.apply_async') as dispatch:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(reverse('library'), {
                    'title': 'Fresh notes', 'file': SimpleUploadedFile('fresh.txt', b'Buoyancy notes', 'text/plain'),
                })
            self.assertEqual(response.status_code, 302)
            doc = Document.objects.get(title='Fresh notes')
            self.assertEqual(doc.owner_id, self.user.pk)
            self.assertEqual(doc.status, 'queued')
            dispatch.assert_called_once()
            self.assertEqual(dispatch.call_args.kwargs['args'][0], doc.pk)
