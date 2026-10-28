from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Attempt, ExamPaper, Question


STORAGES = {'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
            'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}


@override_settings(TIME_ZONE='Asia/Manila', STORAGES=STORAGES)
class ProgressTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('progress-reader')
        self.other = User.objects.create_user('another-reader')
        self.client.force_login(self.user)
        self.question = Question.objects.create(owner=self.user, topic='Stability', prompt='Buoyancy?',
            choices=['a','b','c','d'], correct_index=0, reviewed=True)

    def attempt(self, when, correct=True, owner=None, question=None):
        row = Attempt.objects.create(owner=owner or self.user, question=question or self.question,
                                     selected_index=0 if correct else 1, correct=correct)
        Attempt.objects.filter(pk=row.pk).update(created_at=datetime.fromisoformat(when).replace(tzinfo=ZoneInfo('Asia/Manila')))
        return row

    def get(self, **query):
        with patch('learning.progress_views.timezone.localdate', return_value=date(2026, 10, 28)):
            return self.client.get(reverse('progress'), query)

    def test_default_month_and_empty_state_are_real_and_private(self):
        self.attempt('2026-10-20T10:00', owner=self.other)
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['start_date'], date(2026, 10, 1))
        self.assertEqual(response.context['end_date'], date(2026, 10, 31))
        self.assertEqual(response.context['summary']['total'], 0)
        self.assertIsNone(response.context['accuracy'])
        self.assertEqual(len(response.context['chart']), 31)
        self.assertContains(response, 'No practice recorded')
        self.assertEqual(response.context['active_section'], 'progress')

    def test_month_respects_local_midnight_and_includes_the_last_day(self):
        self.attempt('2026-10-01T00:00')
        self.attempt('2026-10-31T23:59:59', correct=False)
        self.attempt('2026-09-30T23:59:59')
        self.attempt('2026-11-01T00:00')
        response = self.get(period='month', month='2026-10')
        self.assertEqual(response.context['summary'], {'total':2, 'correct':1, 'questions':1})
        self.assertEqual(response.context['accuracy'], 50)
        self.assertEqual(response.context['active_days'], 2)
        self.assertEqual(sum(item['total'] for item in response.context['chart']), 2)
        self.assertEqual(response.context['date_rows'][0]['day'], date(2026,10,31))

    def test_week_starts_monday_and_can_cross_a_month_boundary(self):
        for stamp in ('2026-10-25T23:59:59','2026-10-26T00:00','2026-11-01T23:59:59','2026-11-02T00:00'):
            self.attempt(stamp)
        response = self.get(period='week', date='2026-10-28')
        self.assertEqual((response.context['start_date'], response.context['end_date']), (date(2026,10,26),date(2026,11,1)))
        self.assertEqual(response.context['summary']['total'], 2)
        self.assertEqual(len(response.context['chart']), 7)
        self.assertIn('date=2026-10-19', response.context['navigation']['previous'])

    def test_year_has_twelve_months_and_supports_leap_days(self):
        self.attempt('2024-02-29T12:00')
        self.attempt('2024-12-31T23:59:59', correct=False)
        self.attempt('2025-01-01T00:00')
        response = self.get(period='year', year='2024')
        self.assertEqual(response.context['summary']['total'], 2)
        self.assertEqual(len(response.context['chart']), 12)
        self.assertEqual(response.context['chart'][1]['total'], 1)
        self.assertEqual(response.context['chart'][-1]['total'], 1)
        self.assertIn('year=2025', response.context['navigation']['next'])

    def test_custom_dates_paginate_daily_history_and_keep_filters(self):
        for day in range(1,26):
            self.attempt(f'2026-10-{day:02d}T12:00')
        response = self.get(period='range', start='2026-10-01', end='2026-10-25', page=2)
        self.assertEqual(response.context['summary']['total'], 25)
        self.assertEqual(response.context['date_rows'].paginator.count, 25)
        self.assertEqual(len(response.context['date_rows']), 5)
        self.assertContains(response, 'start=2026-10-01')
        self.assertContains(response, 'end=2026-10-25')
        self.assertContains(response, 'period=range')

    def test_invalid_dates_and_excessive_ranges_are_safe_validation_errors(self):
        cases = [dict(period='bad'), dict(period='month', month='2026-13'), dict(period='year', year='9999'),
                 dict(period='range', start='2026-10-20', end='2026-10-01'),
                 dict(period='range', start='1900-01-01', end='2026-10-01'),
                 dict(period='week', date='9999-12-31')]
        for query in cases:
            with self.subTest(query=query):
                response = self.get(**query)
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.context['filter_form'].errors)

    def test_topic_and_history_never_include_another_accounts_data(self):
        secret = Question.objects.create(owner=self.other, topic='SECRET TOPIC', prompt='Private question', choices=['a','b','c','d'])
        self.attempt('2026-10-20T12:00')
        self.attempt('2026-10-20T12:00', owner=self.other, question=secret)
        # Also guard against a malformed cross-account attempt inserted by hand.
        self.attempt('2026-10-20T12:00', question=secret)
        response = self.get()
        self.assertEqual(response.context['summary']['total'], 1)
        self.assertNotContains(response, 'SECRET TOPIC')
        self.assertNotContains(response, 'Private question')

    def test_deleted_paper_attempts_disappear_from_selected_period(self):
        paper = ExamPaper.objects.create(owner=self.user, title='Delete me', topic='Stability')
        self.question.paper = paper
        self.question.save(update_fields=['paper'])
        self.attempt('2026-10-20T12:00')
        self.assertEqual(self.get().context['summary']['total'], 1)
        self.client.post(reverse('delete_paper', args=[paper.pk]), {'confirm_delete':'yes'})
        self.assertEqual(self.get().context['summary']['total'], 0)

    def test_login_is_required_and_long_ranges_use_bounded_chart_bins(self):
        response = self.get(period='range', start='2020-01-01', end='2026-10-28')
        self.assertLessEqual(len(response.context['chart']), 121)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('progress')).status_code, 302)


@override_settings(STORAGES=STORAGES)
class RefreshedAuthTests(TestCase):
    def test_login_errors_and_successful_next_redirect(self):
        User.objects.create_user('voyage-reader', password='Maritime-test-481!')
        response = self.client.post(reverse('login'), {'username':'voyage-reader', 'password':'wrong'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Please enter a correct username and password')
        response = self.client.post(reverse('login'), {'username':'voyage-reader','password':'Maritime-test-481!','next':'/progress/'})
        self.assertRedirects(response, '/progress/', fetch_redirect_response=False)

    def test_registration_mismatch_then_success(self):
        payload={'username':'new-voyage-reader','password1':'Maritime-test-481!','password2':'mismatch'}
        response=self.client.post(reverse('register'), payload)
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.context['form'].errors)
        self.assertFalse(User.objects.filter(username=payload['username']).exists())
        payload['password2']=payload['password1']
        response=self.client.post(reverse('register'),payload)
        self.assertRedirects(response,reverse('dashboard'),fetch_redirect_response=False)
        self.assertIn('_auth_user_id',self.client.session)
