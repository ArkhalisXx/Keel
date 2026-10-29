"""Serverless job requests preserve private scope and existing task guarantees."""
import tempfile
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from .models import Document, ExamPaper
from .reviewers import generate_reviewer, queue_reviewer
from .tasks import process_document, queue_document


@override_settings(SERVERLESS_JOBS=True, OCR_ENABLED=False, STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class ServerlessJobTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        storage = override_settings(MEDIA_ROOT=directory.name)
        storage.enable()
        self.addCleanup(storage.disable)
        self.user = get_user_model().objects.create_user('serverless-reader')
        self.other = get_user_model().objects.create_user('other-reader')
        self.client.force_login(self.user)
        self.document = Document.objects.create(owner=self.user, title='Notes',
            file=SimpleUploadedFile('notes.txt', b'Buoyancy supports a floating vessel.'))

    def paper(self, **fields):
        values = dict(owner=self.user, document=self.document, title='Reviewer', topic='Ships',
                      kind='reviewer', generation_status='queued', requested_count=1)
        values.update(fields)
        return ExamPaper.objects.create(**values)

    def test_uploaded_work_is_persisted_without_using_celery(self):
        paper = self.paper()
        with patch('learning.tasks.process_document.apply_async') as publish_document, \
                patch('learning.reviewers.generate_reviewer.delay') as publish_reviewer, \
                self.captureOnCommitCallbacks(execute=True):
            queue_document(self.document)
            queue_reviewer(paper)
        publish_document.assert_not_called()
        publish_reviewer.assert_not_called()
        self.document.refresh_from_db()
        paper.refresh_from_db()
        self.assertEqual(self.document.status, 'queued')
        self.assertEqual(paper.generation_status, 'queued')

    def test_document_processes_once_and_returns_no_extracted_content(self):
        response = self.client.post(f'/documents/{self.document.pk}/process/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ready')
        self.assertNotIn('pages', response.json())
        self.document.refresh_from_db()
        self.assertIn('Buoyancy', self.document.pages[0]['text'])
        with patch('learning.serverless_jobs.process_document.run') as process:
            self.assertEqual(self.client.post(f'/documents/{self.document.pk}/process/').json()['status'], 'ready')
        process.assert_not_called()

    def test_get_requests_never_process_jobs(self):
        paper = self.paper()
        with patch('learning.serverless_jobs.process_document.run') as process, \
                patch('learning.serverless_jobs.generate_reviewer.run') as generate:
            self.assertEqual(self.client.get(f'/documents/{self.document.pk}/process/').status_code, 405)
            self.assertEqual(self.client.get(f'/papers/{paper.pk}/process/').status_code, 405)
            self.assertEqual(self.client.get(f'/documents/{self.document.pk}/status/').status_code, 200)
            self.assertEqual(self.client.get(f'/papers/{paper.pk}/generation/').status_code, 200)
        process.assert_not_called()
        generate.assert_not_called()

    def test_owner_and_authentication_required(self):
        paper = self.paper()
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(f'/documents/{self.document.pk}/process/').status_code, 404)
        self.assertEqual(self.client.post(f'/papers/{paper.pk}/process/').status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.post(f'/documents/{self.document.pk}/process/').status_code, 302)
        self.assertEqual(self.client.post(f'/papers/{paper.pk}/process/').status_code, 302)

    def test_processing_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(f'/documents/{self.document.pk}/process/').status_code, 403)
        client.get('/library/')
        token = client.cookies['csrftoken'].value
        response = client.post(f'/documents/{self.document.pk}/process/', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.json()['status'], 'ready')

    def test_stale_document_claim_cannot_process_a_new_attempt(self):
        old = self.document.processing_token
        fresh = uuid.uuid4()
        original_run = process_document.run

        def superseded(pk, token):
            self.assertEqual(token, str(old))
            Document.objects.filter(pk=pk).update(processing_token=fresh)
            original_run(pk, token)

        with patch('learning.serverless_jobs.process_document.run', side_effect=superseded), \
                patch('learning.tasks.extract_pages') as extract:
            response = self.client.post(f'/documents/{self.document.pk}/process/')
        extract.assert_not_called()
        self.assertEqual(response.json()['status'], 'queued')
        self.document.refresh_from_db()
        self.assertEqual(self.document.processing_token, fresh)

    def test_reviewer_processes_only_one_batch_per_post(self):
        self.document.status = 'ready'
        self.document.pages = [{'page': 1, 'text': 'Buoyancy supports a ship.'}]
        self.document.save()
        paper = self.paper(requested_count=12)

        def batch(document, count, **scope):
            return [{'prompt': f'Question {scope["offset"] + i + 1}?',
                     'choices': ['Buoyancy', 'Drag', 'Thrust', 'Friction'], 'correct_index': 0,
                     'explanation': 'The source describes buoyancy.', 'source_page': 1}
                    for i in range(count)]

        with patch('learning.reviewers.generate_items', side_effect=batch) as generate, \
                patch('learning.reviewers.generate_reviewer.delay') as publish, \
                self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(f'/papers/{paper.pk}/process/')
        self.assertEqual(response.json()['generated_count'], 5)
        self.assertEqual(response.json()['status'], 'queued')
        self.assertEqual(paper.questions.count(), 5)
        generate.assert_called_once()
        publish.assert_not_called()

    def test_stale_reviewer_claim_cannot_generate_for_a_new_attempt(self):
        paper = self.paper()
        old = paper.generation_token
        fresh = uuid.uuid4()
        original_run = generate_reviewer.run

        def superseded(pk, token):
            self.assertEqual(token, str(old))
            ExamPaper.objects.filter(pk=pk).update(generation_token=fresh)
            original_run(pk, token)

        with patch('learning.serverless_jobs.generate_reviewer.run', side_effect=superseded), \
                patch('learning.reviewers.generate_items') as generate:
            response = self.client.post(f'/papers/{paper.pk}/process/')
        generate.assert_not_called()
        self.assertEqual(response.json()['status'], 'queued')

    def test_active_or_failed_jobs_are_not_automatically_retried(self):
        for state in ['processing', 'failed']:
            Document.objects.filter(pk=self.document.pk).update(status=state)
            paper = self.paper(generation_status=state)
            with patch('learning.serverless_jobs.process_document.run') as process, \
                    patch('learning.serverless_jobs.generate_reviewer.run') as generate:
                self.assertEqual(self.client.post(f'/documents/{self.document.pk}/process/').json()['status'], state)
                self.assertEqual(self.client.post(f'/papers/{paper.pk}/process/').json()['status'], state)
            process.assert_not_called()
            generate.assert_not_called()

    def test_unexpected_processing_errors_are_generic(self):
        with patch('learning.serverless_jobs.process_document.run', side_effect=RuntimeError('private-credential')):
            response = self.client.post(f'/documents/{self.document.pk}/process/')
        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, 'private-credential', status_code=503)

    def test_document_download_redirects_to_private_storage_after_owner_check(self):
        with patch.object(self.document.file.storage, 'url', return_value='https://storage.example.test/notes?signature=test') as url:
            response = self.client.get(f'/documents/{self.document.pk}/')
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response['Location'], 'https://storage.example.test/notes?signature=test')
            self.assertEqual(response['Cache-Control'], 'private, no-store')
            self.client.force_login(self.other)
            self.assertEqual(self.client.get(f'/documents/{self.document.pk}/').status_code, 404)
        url.assert_called_once()

    @override_settings(SERVERLESS_JOBS=False)
    def test_local_mode_uses_celery_and_does_not_expose_serverless_processing(self):
        paper = self.paper()
        with patch('learning.tasks.process_document.apply_async') as publish_document, \
                patch('learning.reviewers.generate_reviewer.delay') as publish_reviewer, \
                self.captureOnCommitCallbacks(execute=True):
            queue_document(self.document)
            queue_reviewer(paper)
        publish_document.assert_called_once()
        publish_reviewer.assert_called_once()
        self.assertEqual(self.client.post(f'/documents/{self.document.pk}/process/').status_code, 404)
        self.assertEqual(self.client.post(f'/papers/{paper.pk}/process/').status_code, 404)
        response = self.client.get(f'/documents/{self.document.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.streaming)
        self.assertIn(b'Buoyancy', b''.join(response.streaming_content))
