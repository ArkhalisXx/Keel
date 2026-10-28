"""Single-replica queue runner for small free-tier installations.

Rows survive idle shutdowns. Each job runs in a bounded child process; failures
require an explicit user retry, avoiding automatic repeat AI charges.
"""
import subprocess
import sys
import time
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.utils import timezone
from learning.models import Document, ExamPaper


def fail_job(kind, pk, token):
    if kind == 'document':
        Document.objects.filter(pk=pk, processing_token=token, status__in=['queued', 'processing']).update(
            status='failed', processing_deadline=None,
            processing_error='Processing was interrupted or exceeded this server’s limit. Try a smaller document and choose Retry processing.')
    else:
        ExamPaper.objects.filter(pk=pk, generation_token=token, generation_status__in=['queued', 'processing']).update(
            generation_status='failed', generation_deadline=None,
            generation_error='Generation was interrupted or timed out. Completed questions are saved. Choose Retry to continue.')


def expire_jobs():
    now = timezone.now()
    Document.objects.filter(status='processing', processing_deadline__lt=now).update(
        status='failed', processing_deadline=None,
        processing_error='Processing was interrupted. Choose Retry processing.')
    ExamPaper.objects.filter(generation_status='processing', generation_deadline__lt=now).update(
        generation_status='failed', generation_deadline=None,
        generation_error='Generation was interrupted. Completed questions are saved. Choose Retry to continue.')


def run_one(kind):
    if kind == 'document':
        row = Document.objects.filter(status='queued').order_by('queued_at').values_list('pk', 'processing_token').first()
        timeout = settings.DOCUMENT_PROCESSING_TIMEOUT + 15
    else:
        row = ExamPaper.objects.filter(kind='reviewer', generation_status='queued').order_by('created_at').values_list('pk', 'generation_token').first()
        timeout = 120
    if row is None:
        return False
    pk, token = row
    try:
        result = subprocess.run([sys.executable, str(settings.BASE_DIR / 'manage.py'),
            'run_database_job', kind, str(pk), str(token)], timeout=timeout, check=False)
        if result.returncode:
            fail_job(kind, pk, token)
    except subprocess.TimeoutExpired:
        fail_job(kind, pk, token)
    finally:
        close_old_connections()
    return True


class Command(BaseCommand):
    help = 'Process durable database jobs while the web service is awake. Use one replica only.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')

    def handle(self, **options):
        if settings.JOB_BACKEND != 'database':
            raise CommandError('This command requires JOB_BACKEND=database.')
        while True:
            close_old_connections()
            expire_jobs()
            # Give document processing and reviewer batches a turn each cycle.
            documents = run_one('document')
            reviewers = run_one('reviewer')
            if options['once']:
                return
            if not (documents or reviewers):
                time.sleep(3)
