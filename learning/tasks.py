"""Each processing attempt has a token; stale workers cannot overwrite a newer attempt."""
from datetime import timedelta
import time
import uuid

from billiard.exceptions import SoftTimeLimitExceeded
from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Document
from .services import DocumentExtractionError, ProcessingCancelled, extract_pages


def dispatch_document(pk, token):
    try:
        process_document.apply_async(args=[pk, str(token)], task_id=str(token))
    except Exception:
        Document.objects.filter(pk=pk, processing_token=token, status=Document.Status.QUEUED).update(
            status=Document.Status.FAILED,
            processing_error='The processing queue is unavailable. Start Redis and the worker, then choose Retry processing.')


def queue_document(document):
    """Persist the attempt before publishing; no worker can race an uncommitted upload."""
    token = uuid.uuid4()
    Document.objects.filter(pk=document.pk).update(
        status=Document.Status.QUEUED, processing_token=token, processing_error='',
        queued_at=timezone.now(), processing_deadline=None, pages_processed=0,
        total_pages=0, ocr_page_count=0, pages=[])
    transaction.on_commit(lambda: dispatch_document(document.pk, token))


@shared_task(ignore_result=True)
def process_document(pk, token):
    current = Document.objects.filter(pk=pk, processing_token=token)
    claimed = current.filter(status=Document.Status.QUEUED).update(
        status=Document.Status.PROCESSING,
        processing_deadline=timezone.now() + timedelta(seconds=settings.DOCUMENT_PROCESSING_TIMEOUT + 60))
    if not claimed:
        return
    start = time.monotonic()
    last_save = 0

    def progress(done, total):
        nonlocal last_save
        now = time.monotonic()
        if now - start > settings.DOCUMENT_PROCESSING_TIMEOUT:
            raise DocumentExtractionError('Document processing exceeded its time limit. Split the document and retry.')
        # Save at most once per second, plus the first and last page.
        if done in (0, total) or now - last_save >= 1:
            if not current.filter(status=Document.Status.PROCESSING).update(pages_processed=done, total_pages=total):
                raise ProcessingCancelled()
            last_save = now

    try:
        document = current.only('file').first()
        if document is None:
            return
        with document.file.open('rb') as file:
            pages = extract_pages(file, progress=progress)
        current.filter(status=Document.Status.PROCESSING).update(
            pages=pages, status=Document.Status.READY, processing_error='',
            total_pages=len(pages), pages_processed=len(pages),
            ocr_page_count=sum(p.get('source') == 'ocr' for p in pages), processing_deadline=None)
    except ProcessingCancelled:
        return
    except (DocumentExtractionError, SoftTimeLimitExceeded) as error:
        message = str(error) if isinstance(error, DocumentExtractionError) else 'Processing timed out. Split the document and retry.'
        current.filter(status=Document.Status.PROCESSING).update(
            status=Document.Status.FAILED, processing_error=message, processing_deadline=None)
    except Exception:
        # Do not expose storage paths, document contents or infrastructure details.
        current.filter(status=Document.Status.PROCESSING).update(
            status=Document.Status.FAILED, processing_deadline=None,
            processing_error='Document processing failed unexpectedly. Check that the worker can access the uploaded file, then retry.')
