from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from .models import Attempt, Document, ExamPaper, PracticeRun, Question


@override_settings(
    TIME_ZONE='Asia/Manila',
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
class DashboardDataTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('dashboard-reader')
        self.other = User.objects.create_user('another-reader')
        self.client.force_login(self.user)
        self.shared = self.question('Shared question')
        self.owned = self.question('Owned question', owner=self.user)
        self.private = self.question('Private question', owner=self.other)

    def question(self, prompt, **kwargs):
        return Question.objects.create(
            topic='Hydrostatics', prompt=prompt, choices=['a', 'b', 'c', 'd'],
            correct_index=0, explanation='Source explanation', reviewed=True, **kwargs,
        )

    def attempt(self, question, correct, *, user=None, when=None):
        attempt = Attempt.objects.create(owner=user or self.user, question=question,
                                         selected_index=0 if correct else 1, correct=correct)
        if when:
            Attempt.objects.filter(pk=attempt.pk).update(created_at=when)
        return attempt

    def dashboard(self):
        with patch('learning.views.timezone.localdate', return_value=date(2026, 10, 28)):
            return self.client.get('/')

    def test_empty_progress_is_not_exam_readiness_or_fabricated_activity(self):
        doc = Document.objects.create(owner=self.user, title='Ready reference', file='ready.txt', status='ready')
        Document.objects.create(owner=self.user, title='Still processing', file='pending.txt', status='processing')
        Document.objects.create(owner=self.other, title='Private reference', file='private.txt', status='ready')
        ExamPaper.objects.create(owner=self.other, title='Private exam', topic='Other')
        response = self.dashboard()
        self.assertEqual(response.status_code, 200)
        context = response.context
        self.assertIsNone(context['accuracy'])
        self.assertAlmostEqual(context['accuracy_ring_offset'], 314.159, places=3)
        self.assertEqual(context['total'], 0)
        self.assertEqual(context['active_days'], 0)
        self.assertEqual(context['weekly_goal'], 7)
        self.assertEqual(context['topics_progress'], [])
        self.assertIsNone(context['focus_topic'])
        self.assertIsNone(context['latest_run'])
        self.assertEqual(context['documents'], 2)
        self.assertEqual(context['ready_documents'], 1)
        self.assertEqual(context['latest_document'].pk, doc.pk)
        self.assertEqual(context['question_count'], 2)
        self.assertEqual(context['unattempted_count'], 2)
        self.assertEqual(context['paper_count'], 0)
        self.assertNotContains(response, 'Private reference')

    def test_review_and_unattempted_counts_use_latest_own_answer_per_visible_question(self):
        untouched = self.question('Untouched question')
        draft = self.question('Draft question', owner=self.user)
        draft.reviewed = False
        draft.save(update_fields=['reviewed'])
        same_time = datetime(2026, 10, 27, 10, tzinfo=ZoneInfo('Asia/Manila'))
        self.attempt(self.shared, False, when=same_time)
        self.attempt(self.shared, True, when=same_time)
        self.attempt(self.shared, False, user=self.other)
        self.attempt(self.owned, False)
        self.attempt(self.owned, False)
        self.attempt(draft, False)
        self.attempt(untouched, False, user=self.other)
        self.attempt(self.private, False, user=self.other)
        context = self.dashboard().context
        self.assertEqual(context['question_count'], 3)
        self.assertEqual(context['review_count'], 1)
        self.assertEqual(context['unattempted_count'], 1)
        self.assertEqual(context['total'], 5)
        self.assertEqual(context['accuracy'], 20)
        self.assertAlmostEqual(context['accuracy_ring_offset'], 251.327, places=3)

    def test_week_is_monday_to_sunday_in_local_timezone_and_excludes_future_attempts(self):
        tz = ZoneInfo('Asia/Manila')
        self.attempt(self.shared, True, when=datetime(2026, 10, 26, 9, tzinfo=tz))
        self.attempt(self.shared, True, when=datetime(2026, 10, 26, 10, tzinfo=tz))
        # This is still Tuesday in UTC but Wednesday for the configured study day.
        self.attempt(self.shared, False, when=datetime(2026, 10, 28, 1, tzinfo=tz))
        self.attempt(self.shared, True, when=datetime(2026, 10, 29, 10, tzinfo=tz))
        self.attempt(self.shared, True, user=self.other, when=datetime(2026, 10, 27, 10, tzinfo=tz))
        context = self.dashboard().context
        days = context['week_days']
        self.assertEqual([day['label'] for day in days], ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'])
        self.assertEqual([day['count'] for day in days], [2, 0, 1, 0, 0, 0, 0])
        self.assertEqual([day['is_today'] for day in days], [False, False, True, False, False, False, False])
        self.assertEqual([day['is_future'] for day in days], [False, False, False, True, True, True, True])
        self.assertEqual(context['active_days'], 2)
        self.assertEqual(sum(day['count'] for day in context['chart_days']), 3)

    def test_continue_session_summary_is_owned_incomplete_and_excludes_answer_keys(self):
        question = {'id': self.shared.pk, 'prompt': 'Private snapshot?', 'correct_index': 2, 'choices': ['a', 'b', 'c', 'd']}
        run = PracticeRun.objects.create(owner=self.user, title='Continue my set', questions=[question], results=[question])
        PracticeRun.objects.create(owner=self.user, title='Finished', questions=[question], completed=True)
        PracticeRun.objects.create(owner=self.other, title='Other user session', questions=[question])
        context = self.dashboard().context
        self.assertEqual(context['latest_run'], {'id': run.pk, 'title': run.title, 'answered': 1, 'total': 1})
        self.assertNotIn('correct_index', str(context['latest_run']))

    def test_recent_practice_is_limited_to_four_and_does_not_show_other_users_answers(self):
        for _ in range(6):
            self.attempt(self.shared, True)
        self.attempt(self.private, False, user=self.other)
        context = self.dashboard().context
        self.assertEqual(len(context['recent']), 4)
        self.assertTrue(all(attempt.owner_id == self.user.pk for attempt in context['recent']))
        self.assertEqual(context['topics_progress'], [{'topic': 'Hydrostatics', 'total': 6, 'accuracy': 100}])
        self.assertEqual(context['focus_topic']['topic'], 'Hydrostatics')
