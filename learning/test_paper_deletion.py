from unittest.mock import patch

from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import Client, TestCase, override_settings
from django.urls import include, path

from .models import Attempt, Document, ExamPaper, PracticeRun, Question
from .paper_deletion import delete_paper

urlpatterns = [
    path('papers/<int:pk>/delete/', delete_paper, name='delete_paper'),
    path('', include('config.urls')),
]


@override_settings(ROOT_URLCONF=__name__, STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class PaperDeletionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('paper-owner')
        self.other = User.objects.create_user('other-owner')
        self.client.force_login(self.user)
        self.document = Document.objects.create(owner=self.user, title='Source textbook',
                                                file='source.txt', status='ready')
        self.paper = ExamPaper.objects.create(owner=self.user, document=self.document,
                                              title='Chapter 3 reviewer', topic='Stability')
        self.question = Question.objects.create(owner=self.user, paper=self.paper,
            source_document=self.document, topic='Stability', prompt='Why does a ship float?',
            choices=['Buoyancy', 'Friction', 'Drag', 'Wind'], correct_index=0, reviewed=True)
        self.other_paper = ExamPaper.objects.create(owner=self.user, title='Keep this paper', topic='Math')
        self.other_question = Question.objects.create(owner=self.user, paper=self.other_paper,
            topic='Math', prompt='One plus one?', choices=['1', '2', '3', '4'], correct_index=1, reviewed=True)
        self.attempt = Attempt.objects.create(owner=self.user, question=self.question,
                                              selected_index=0, correct=True)
        self.kept_attempt = Attempt.objects.create(owner=self.user, question=self.other_question,
                                                   selected_index=1, correct=True)
        self.run = PracticeRun.objects.create(owner=self.user, title='Affected session',
                                               questions=[{'id': self.question.pk}])
        self.mixed_run = PracticeRun.objects.create(owner=self.user, title='Mixed session',
            completed=True, questions=[{'id': self.question.pk}, {'id': self.other_question.pk}],
            results=[{'id': self.question.pk}])
        self.kept_run = PracticeRun.objects.create(owner=self.user, title='Kept session',
                                                    questions=[{'id': self.other_question.pk}])
        self.url = f'/papers/{self.paper.pk}/delete/'

    def test_get_explains_exact_impact_without_deleting(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Chapter 3 reviewer')
        self.assertEqual(response.context['question_count'], 1)
        self.assertEqual(response.context['attempt_count'], 1)
        self.assertEqual(response.context['session_count'], 2)
        self.assertContains(response, 'This changes your analytics.')
        self.assertContains(response, 'original uploaded document stays in Library')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertTrue(ExamPaper.objects.filter(pk=self.paper.pk).exists())
        self.assertTrue(Attempt.objects.filter(pk=self.attempt.pk).exists())

    def test_explicit_confirmation_is_required_and_csrf_is_enforced(self):
        for value in ('', 'no', 'true', '1'):
            response = self.client.post(self.url, {'confirm_delete': value})
            self.assertEqual(response.status_code, 400)
            self.assertContains(response, 'Confirm that you understand', status_code=400)
            self.assertTrue(ExamPaper.objects.filter(pk=self.paper.pk).exists())
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(self.url, {'confirm_delete': 'yes'}).status_code, 403)

    def test_confirmed_deletion_removes_questions_attempts_and_affected_sessions_only(self):
        response = self.client.post(self.url, {'confirm_delete': 'yes'})
        self.assertRedirects(response, '/papers/import/', fetch_redirect_response=False)
        self.assertFalse(ExamPaper.objects.filter(pk=self.paper.pk).exists())
        self.assertFalse(Question.objects.filter(pk=self.question.pk).exists())
        self.assertFalse(Attempt.objects.filter(pk=self.attempt.pk).exists())
        self.assertFalse(PracticeRun.objects.filter(pk__in=[self.run.pk, self.mixed_run.pk]).exists())
        self.assertTrue(Document.objects.filter(pk=self.document.pk).exists())
        self.assertTrue(ExamPaper.objects.filter(pk=self.other_paper.pk).exists())
        self.assertTrue(Question.objects.filter(pk=self.other_question.pk).exists())
        self.assertTrue(Attempt.objects.filter(pk=self.kept_attempt.pk).exists())
        self.assertTrue(PracticeRun.objects.filter(pk=self.kept_run.pk).exists())
        self.assertEqual(self.client.get(f'/practice/sessions/{self.run.pk}/').status_code, 404)
        self.assertEqual(self.client.post(f'/practice/sessions/{self.run.pk}/',
            {'card': '0', 'action': 'answer', 'answer': '0'}).status_code, 404)
        self.assertEqual(self.client.post(self.url, {'confirm_delete': 'yes'}).status_code, 404)

    def test_owners_cannot_inspect_or_delete_another_users_paper(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, {'confirm_delete': 'yes'}).status_code, 404)
        self.assertTrue(ExamPaper.objects.filter(pk=self.paper.pk).exists())
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_empty_and_queued_papers_can_be_deleted_without_touching_library(self):
        self.paper.questions.all().delete()
        self.paper.kind = 'reviewer'
        self.paper.generation_status = 'queued'
        self.paper.save()
        token = str(self.paper.generation_token)
        response = self.client.post(self.url, {'confirm_delete': 'yes'})
        self.assertEqual(response.status_code, 302)
        from .reviewers import generate_reviewer
        with patch('learning.reviewers.answer_question') as answer:
            generate_reviewer.run(self.paper.pk, token)
        answer.assert_not_called()
        self.assertTrue(Document.objects.filter(pk=self.document.pk).exists())

    def test_stale_collection_link_cannot_start_a_new_session_after_deletion(self):
        with patch('learning.practice_groups.render', return_value=HttpResponse()) as render:
            self.client.get('/practice/')
            groups = list(render.call_args.args[2]['groups'])
            group = next(group for group in groups if group['paper_id'] == self.paper.pk)
        self.client.post(self.url, {'confirm_delete': 'yes'})
        before = PracticeRun.objects.count()
        self.assertEqual(self.client.post('/practice/start/', {'group': group['key']}).status_code, 404)
        self.assertEqual(PracticeRun.objects.count(), before)

    def test_confirmation_does_not_expose_other_users_history(self):
        Attempt.objects.create(owner=self.other, question=self.question, selected_index=0, correct=True)
        PracticeRun.objects.create(owner=self.other, title='Private unrelated history',
                                     questions=[{'id': self.question.pk}])
        response = self.client.get(self.url)
        self.assertEqual(response.context['attempt_count'], 1)
        self.assertEqual(response.context['session_count'], 2)
        self.assertNotContains(response, 'Private unrelated history')
