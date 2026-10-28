"""Scoped creation, private source metadata, and paper-library integration."""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .exam_forms import PaperImportForm
from .models import Document, ExamPaper


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class PaperWorkspaceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('paper-workspace')
        self.other = User.objects.create_user('private-workspace')
        self.client.force_login(self.user)
        self.doc = Document.objects.create(owner=self.user, title='Naval architecture notes', file='notes.txt',
            status='ready', pages=[
                {'page': 1, 'text': 'Chapter 1: Buoyancy\nBuoyancy is the upward force on a floating body.'},
                {'page': 2, 'text': 'Chapter 2: Transverse Stability\nMetacentric height describes initial stability.'},
                {'page': 3, 'text': 'Transverse stability depends on the relative positions of G and M.'},
            ])

    def data(self, **changes):
        data = {'document': self.doc.pk, 'kind': 'reviewer', 'title': 'Stability reviewer',
                'topic': 'Stability', 'question_count': '50', 'focus': 'Transverse stability',
                'page_start': '2', 'page_end': '3'}
        data.update(changes)
        return data

    @patch('learning.reviewers.generate_reviewer.delay')
    def test_fifty_questions_and_multiple_scopes_are_saved(self, dispatch):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('import_paper'), self.data())
        self.assertEqual(response.status_code, 302)
        paper = ExamPaper.objects.get(owner=self.user)
        self.assertEqual((paper.requested_count, paper.source_page_start, paper.source_page_end), (50, 2, 3))
        self.assertEqual(paper.generation_focus, 'Transverse stability')
        self.assertEqual(paper.generation_status, 'queued')
        dispatch.assert_called_once()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('import_paper'), self.data(
                title='Buoyancy reviewer', focus='Buoyancy', page_start='1', page_end='1'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ExamPaper.objects.filter(owner=self.user, document=self.doc).count(), 2)

    def test_invalid_count_or_scope_never_queues_or_creates(self):
        for changes in ({'question_count': '101'}, {'question_count': '0'},
                        {'page_start': '3', 'page_end': '2'}, {'page_end': '999'},
                        {'focus': 'NoMatchingTermInThisDocument'}):
            with self.subTest(changes=changes), patch('learning.reviewers.generate_reviewer.delay') as dispatch:
                form = PaperImportForm(self.data(**changes), user=self.user)
                self.assertFalse(form.is_valid())
                response = self.client.post(reverse('import_paper'), self.data(**changes))
                self.assertEqual(response.status_code, 200)
                dispatch.assert_not_called()
        self.assertFalse(ExamPaper.objects.exists())

    @patch('learning.reviewers.generate_reviewer.delay')
    @patch('learning.exam_views.import_document')
    def test_scoped_automatic_mode_uses_reviewer_instead_of_ignoring_scope(self, imported, dispatch):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('import_paper'), self.data(kind='auto'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ExamPaper.objects.get().kind, 'reviewer')
        imported.assert_not_called()
        dispatch.assert_called_once()

    def test_source_outline_is_owned_ready_and_contains_no_excerpts(self):
        url = reverse('paper_source', args=[self.doc.pk])
        data = self.client.get(url).json()
        self.assertEqual((data['page_min'], data['page_max']), (1, 3))
        self.assertTrue(any(chapter['start'] == 2 for chapter in data['chapters']))
        self.assertNotIn('upward force', str(data))
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.user)
        self.doc.status = 'processing'
        self.doc.save(update_fields=['status'])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_progress_is_private_and_matches_saved_counts(self):
        paper = ExamPaper.objects.create(owner=self.user, document=self.doc, kind='reviewer',
            title='Working reviewer', topic='Stability', requested_count=50, generated_count=10,
            generation_status='processing')
        url = reverse('reviewer_status', args=[paper.pk])
        data = self.client.get(url).json()
        self.assertEqual((data['generated_count'], data['requested_count']), (10, 50))
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_paper_search_filters_pagination_and_private_stats(self):
        for i in range(10):
            ExamPaper.objects.create(owner=self.user, kind='reviewer', title=f'Stability set {i}', topic='Hydrostatics')
        ExamPaper.objects.create(owner=self.other, title='Private paper', topic='Secret')
        response = self.client.get(reverse('import_paper'))
        self.assertEqual(response.context['paper_stats']['total'], 10)
        self.assertEqual(len(response.context['papers']), 8)
        self.assertEqual(response.context['paper_page'].paginator.num_pages, 2)
        self.assertNotContains(response, 'Private paper')
        response = self.client.get(reverse('import_paper'), {'paper_search': 'set 9', 'paper_kind': 'reviewer'})
        self.assertEqual(len(response.context['papers']), 1)
        self.assertContains(response, 'Stability set 9')
        self.assertEqual(response.context['paper_stats']['total'], 10)
