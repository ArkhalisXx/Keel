from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from .forms import UploadForm, TutorForm
from .models import Document, Question, Attempt
from .services import DocumentExtractionError, extract_pages, retrieve, explain
from django.conf import settings
from django.db import transaction
from .tasks import queue_document
from .exams import visible_questions
from .models import ExamPaper
from django.core.paginator import Paginator
from django.db.models import Count, Q, OuterRef, Subquery
from django.db.models.functions import TruncDate
from django.utils import timezone
from datetime import timedelta


def register(request):
    form = UserCreationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        login(request, form.save())
        return redirect('dashboard')
    return render(request, 'registration/register.html', {'form':form})

@login_required
def dashboard(request):
    attempts = Attempt.objects.filter(owner=request.user)
    total = attempts.count()
    today = timezone.localdate()
    start = today - timedelta(days=6)
    daily = {row['day']: row['count'] for row in attempts.filter(created_at__date__gte=start).annotate(day=TruncDate('created_at')).values('day').annotate(count=Count('pk'))}
    peak = max(daily.values(), default=1) or 1
    chart = []
    for offset in range(7):
        date = start + timedelta(days=offset)
        count = daily.get(date, 0)
        chart.append({'label': date.strftime('%a'), 'count': count, 'height': round(100 * count / peak)})
    topics = list(attempts.values('question__topic').annotate(total=Count('pk'), correct=Count('pk', filter=Q(correct=True))).order_by('question__topic')[:6])
    documents = Document.objects.filter(owner=request.user)
    return render(request, 'learning/dashboard.html', {
        'documents': documents.count(), 'ready_documents': documents.filter(status=Document.Status.READY).count(),
        'total': total, 'accuracy': round(100 * attempts.filter(correct=True).count() / total) if total else None,
        'recent': attempts.select_related('question').order_by('-created_at')[:5],
        'question_count': visible_questions(request.user).count(), 'paper_count': ExamPaper.objects.filter(owner=request.user).count(),
        'chart_days': chart, 'topics_progress': [{'topic': row['question__topic'], 'total': row['total'], 'accuracy': round(100*row['correct']/row['total'])} for row in topics],
        'latest_document': documents.filter(status=Document.Status.READY).defer('pages').order_by('-created_at').first(),
    })

@login_required
def library(request):
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        doc = form.save(commit=False)
        doc.owner = request.user
        with transaction.atomic():
            doc.save()
            queue_document(doc)
        messages.success(request, 'Document uploaded. Text extraction and OCR will run in the background.')
        return redirect('library')
    docs = Document.objects.filter(owner=request.user).defer('pages')
    search = request.GET.get('q', '').strip()[:200]
    if search:
        docs = docs.filter(title__icontains=search)
    file_type = request.GET.get('type', '')
    if file_type in ('pdf', 'docx', 'txt'):
        docs = docs.filter(file__iendswith='.' + file_type)
    else:
        file_type = ''
    status = request.GET.get('status', '')
    if status in Document.Status.values:
        docs = docs.filter(status=status)
    else:
        status = ''
    sorts = {'newest': '-created_at', 'oldest': 'created_at', 'title': 'title', 'status': 'status'}
    sort = request.GET.get('sort', 'newest')
    if sort not in sorts:
        sort = 'newest'
    documents = Paginator(docs.order_by(sorts[sort], 'pk'), 12).get_page(request.GET.get('page'))
    return render(request, 'learning/library.html', {'form':form, 'documents': documents,
        'upload_limit_mb': settings.DOCUMENT_MAX_UPLOAD_MB, 'pdf_page_limit': settings.DOCUMENT_MAX_PDF_PAGES,
        'search': search, 'file_type': file_type, 'selected_status': status, 'statuses': Document.Status.choices, 'sort': sort})

@login_required
def document(request, pk):
    doc = get_object_or_404(Document, pk=pk, owner=request.user)
    return FileResponse(doc.file.open('rb'), as_attachment=True, filename=doc.title + __import__('pathlib').Path(doc.file.name).suffix)

@login_required
@require_POST
def delete_document(request, pk):
    doc = get_object_or_404(Document, pk=pk, owner=request.user)
    doc.file.delete(save=False)
    doc.delete()
    return redirect('library')

@login_required
def document_status(request, pk):
    doc = get_object_or_404(Document.objects.defer('pages'), pk=pk, owner=request.user)
    return JsonResponse({'status': doc.status, 'label': doc.get_status_display(),
        'processed': doc.pages_processed, 'total': doc.total_pages,
        'ocr_pages': doc.ocr_page_count, 'error': doc.processing_error, 'can_retry': doc.can_retry})


@login_required
@require_POST
def retry_document(request, pk):
    with transaction.atomic():
        doc = get_object_or_404(Document.objects.select_for_update().defer('pages'), pk=pk, owner=request.user)
        if doc.can_retry:
            queue_document(doc)
            messages.info(request, 'Document queued for another processing attempt.')
        else:
            messages.info(request, 'This document is ready or its processing attempt is still active.')
    return redirect('library')


@login_required
def tutor(request):
    form = TutorForm(request.POST or None, user=request.user)
    sources, answer, asked = [], None, False
    if request.method == 'POST' and form.is_valid():
        asked = True
        sources = retrieve(form.cleaned_data['documents'], form.cleaned_data['question'])
        if not sources:
            messages.info(request, 'No matching passages found. Try a more specific question or another document.')
        else:
            try:
                answer = explain(form.cleaned_data['question'], sources)
                if answer is None:
                    messages.info(request, 'AI is not configured yet. You can still review the matching source passages below.')
            except Exception:
                messages.error(request, 'The AI provider could not complete this request. Check the backend provider settings, quota, and network access, then try again.')
    return render(request, 'learning/tutor.html', {'form':form,'sources':sources,'answer':answer,'asked':asked})

@login_required
def practice(request):
    questions = visible_questions(request.user)
    topics = list(questions.values_list('topic', flat=True).distinct().order_by('topic'))
    topic = request.GET.get('topic', '')
    search = request.GET.get('q', '').strip()[:200]
    paper_id = request.GET.get('paper', '')
    papers = ExamPaper.objects.filter(owner=request.user).order_by('title')
    if topic:
        questions = questions.filter(topic=topic)
    if search:
        questions = questions.filter(Q(prompt__icontains=search) | Q(topic__icontains=search))
    if paper_id:
        questions = questions.filter(paper_id=paper_id) if paper_id.isdigit() and papers.filter(pk=paper_id).exists() else questions.none()
    last = Attempt.objects.filter(owner=request.user, question_id=OuterRef('pk')).order_by('-created_at', '-pk')
    questions = questions.annotate(last_correct=Subquery(last.values('correct')[:1]), practice_count=Count('attempt', filter=Q(attempt__owner=request.user)))
    progress = request.GET.get('progress', '')
    if progress == 'new':
        questions = questions.filter(last_correct__isnull=True)
    elif progress == 'review':
        questions = questions.filter(last_correct=False)
    elif progress == 'correct':
        questions = questions.filter(last_correct=True)
    sorts = {'newest': ('-pk',), 'oldest': ('pk',), 'topic': ('topic', 'pk'), 'paper': ('paper_id','position','pk'), 'least': ('practice_count','pk')}
    sort = request.GET.get('sort', 'newest')
    if sort not in sorts:
        sort = 'newest'
    page = Paginator(questions.select_related('paper').order_by(*sorts[sort]), 20).get_page(request.GET.get('page'))
    return render(request, 'learning/practice.html', {'questions': page, 'topics': topics, 'topic': topic,
        'search': search, 'papers': papers, 'paper_id': paper_id, 'progress': progress, 'sort': sort})

@login_required
def question(request, pk):
    q = get_object_or_404(visible_questions(request.user), pk=pk)
    attempt = None
    if request.method == 'POST':
        value = request.POST.get('choice','')
        if value not in ['0','1','2','3']:
            messages.error(request,'Choose an answer first.')
        else:
            attempt = Attempt.objects.create(owner=request.user,question=q,selected_index=int(value),correct=int(value)==q.correct_index)
            return redirect('result', pk=attempt.pk)
    return render(request,'learning/question.html',{'q':q,'choices':enumerate(q.choices)})

@login_required
def result(request, pk):
    attempt = get_object_or_404(Attempt.objects.select_related('question'),pk=pk,owner=request.user)
    q=attempt.question
    return render(request,'learning/result.html',{'attempt':attempt,'q':q,'selected':q.choices[attempt.selected_index],'correct':q.choices[q.correct_index] if q.correct_index is not None else 'Awaiting a reviewed answer'})
