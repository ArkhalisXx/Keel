"""Run one owned, queued job within an explicit request on serverless hosting.

GET status endpoints remain read-only. Existing token-guarded task claims prevent
duplicate processing; interrupted jobs use the ordinary explicit Retry action.
"""
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from .models import Document, ExamPaper
from .tasks import process_document
from .reviewers import generate_reviewer


@login_required
@require_POST
def process_document_serverless(request, pk):
    if not getattr(settings, 'SERVERLESS_JOBS', False):
        raise Http404
    document = get_object_or_404(Document.objects.defer('pages'), pk=pk, owner=request.user)
    if document.status == Document.Status.QUEUED:
        try:
            process_document.run(document.pk, str(document.processing_token))
        except Exception:
            return JsonResponse({'error': 'Processing could not finish. Check the document status before retrying.'}, status=503)
    document = get_object_or_404(Document.objects.defer('pages'), pk=pk, owner=request.user)
    return JsonResponse({
        'status': document.status, 'label': document.get_status_display(),
        'processed': document.pages_processed, 'total': document.total_pages,
        'ocr_pages': document.ocr_page_count, 'error': document.processing_error,
        'can_retry': document.can_retry,
    })


@login_required
@require_POST
def process_reviewer_serverless(request, pk):
    if not getattr(settings, 'SERVERLESS_JOBS', False):
        raise Http404
    paper = get_object_or_404(ExamPaper, pk=pk, owner=request.user, kind='reviewer')
    if paper.generation_status == 'queued':
        try:
            generate_reviewer.run(paper.pk, str(paper.generation_token))
        except Exception:
            return JsonResponse({'error': 'Generation could not finish. Check the reviewer status before retrying.'}, status=503)
    paper = get_object_or_404(ExamPaper, pk=pk, owner=request.user, kind='reviewer')
    return JsonResponse({
        'status': paper.generation_status, 'error': paper.generation_error,
        'can_retry': paper.can_retry_generation, 'generated_count': paper.generated_count,
        'requested_count': paper.requested_count,
    })
