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
    return render(request, 'learning/dashboard.html', {'documents':Document.objects.filter(owner=request.user).count(), 'total':total, 'accuracy':round(100*attempts.filter(correct=True).count()/total) if total else None, 'recent':attempts.select_related('question').order_by('-created_at')[:5]})

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
    return render(request, 'learning/library.html', {'form':form, 'documents':Document.objects.filter(owner=request.user).defer('pages').order_by('-created_at'), 'upload_limit_mb': settings.DOCUMENT_MAX_UPLOAD_MB, 'pdf_page_limit': settings.DOCUMENT_MAX_PDF_PAGES})

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
    topic = request.GET.get('topic','')
    questions = Question.objects.filter(reviewed=True)
    topics = questions.values_list('topic',flat=True).distinct().order_by('topic')
    if topic: questions = questions.filter(topic=topic)
    return render(request, 'learning/practice.html', {'questions':questions, 'topics':topics, 'topic':topic})

@login_required
def question(request, pk):
    q = get_object_or_404(Question, pk=pk, reviewed=True)
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
    return render(request,'learning/result.html',{'attempt':attempt,'q':q,'selected':q.choices[attempt.selected_index],'correct':q.choices[q.correct_index]})
