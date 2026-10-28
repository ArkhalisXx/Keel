import subprocess
import tempfile
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from learning.models import Document, ExamPaper
from learning.tasks import queue_document
from learning.reviewers import queue_reviewer
from learning.management.commands.run_database_worker import expire_jobs, fail_job, run_one


@override_settings(JOB_BACKEND='database')
class DatabaseWorkerTests(TransactionTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage = override_settings(MEDIA_ROOT=self.directory.name)
        self.storage.enable()
        self.addCleanup(self.storage.disable)
        self.user = get_user_model().objects.create_user('reader')
        self.document = Document.objects.create(owner=self.user, title='Notes',
            file=SimpleUploadedFile('notes.txt', b'Buoyancy supports a floating vessel.'))

    def test_document_queue_is_durable_without_redis(self):
        with patch('learning.tasks.process_document.apply_async') as celery:
            queue_document(self.document)
        celery.assert_not_called()
        self.document.refresh_from_db()
        call_command('run_database_job', 'document', self.document.pk, str(self.document.processing_token))
        self.document.refresh_from_db()
        self.assertEqual(self.document.status, 'ready')
        self.assertIn('Buoyancy', self.document.pages[0]['text'])

    def test_reviewer_queue_does_not_publish_to_celery(self):
        paper = ExamPaper.objects.create(owner=self.user, document=self.document, kind='reviewer', title='Review', topic='Ships')
        with patch('learning.reviewers.generate_reviewer.delay') as celery:
            queue_reviewer(paper)
        celery.assert_not_called()
        paper.refresh_from_db()
        self.assertEqual(paper.generation_status, 'queued')

    def test_timeout_marks_failure_and_requires_explicit_retry(self):
        with patch('learning.management.commands.run_database_worker.subprocess.run', side_effect=subprocess.TimeoutExpired('job', 120)):
            self.assertTrue(run_one('document'))
        self.document.refresh_from_db()
        self.assertEqual(self.document.status, 'failed')
        self.assertTrue(self.document.can_retry)
        self.assertFalse(run_one('document'))

    def test_old_token_cannot_fail_a_new_attempt(self):
        old_token = self.document.processing_token
        queue_document(self.document)
        fail_job('document', self.document.pk, old_token)
        self.document.refresh_from_db()
        self.assertEqual(self.document.status, 'queued')

    def test_expire_interrupted_jobs_but_preserve_queued_work(self):
        self.document.status = 'processing'
        self.document.processing_deadline = timezone.now() - timedelta(seconds=1)
        self.document.save()
        paper = ExamPaper.objects.create(owner=self.user, document=self.document, kind='reviewer', title='Review', topic='Ships', generation_status='queued', generation_deadline=timezone.now()-timedelta(hours=1))
        expire_jobs()
        self.document.refresh_from_db();paper.refresh_from_db()
        self.assertEqual(self.document.status, 'failed')
        self.assertEqual(paper.generation_status, 'queued')

    def test_empty_queue_returns_without_starting_process(self):
        self.document.delete()
        with patch('learning.management.commands.run_database_worker.subprocess.run') as child:
            self.assertFalse(run_one('document'))
            self.assertFalse(run_one('reviewer'))
        child.assert_not_called()
