from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from .forms import UploadForm, TutorForm
from .models import Document, Question, Attempt
from .services import extract_pages, retrieve, explain


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
        try:
            doc.pages = extract_pages(request.FILES['file'])
        except Exception:
            form.add_error('file', 'Could not read this document. Use a valid text-based PDF, DOCX or UTF-8 TXT (up to 500 pages). Scans need OCR first.')
        else:
            doc.save()
            messages.success(request, 'Document added to your private library.')
            return redirect('library')
    return render(request, 'learning/library.html', {'form':form, 'documents':Document.objects.filter(owner=request.user).order_by('-created_at')})

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
