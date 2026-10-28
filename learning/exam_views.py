from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from .reviewers import create_reviewer, queue_reviewer
from django.shortcuts import get_object_or_404, redirect, render

from .exam_forms import PaperImportForm, QuestionReviewForm
from .exams import PaperImportError, import_document
from .models import ExamPaper, Question

PAPER_SORTS = {'original': ('position', 'pk'), 'topic': ('topic', 'position', 'pk'),
               'drafts': ('reviewed', 'position', 'pk'), 'published': ('-reviewed', 'position', 'pk')}


@login_required
def import_paper(request):
    initial = {}
    document_id = request.GET.get('document', '')
    if request.method == 'GET' and document_id.isdigit():
        from .models import Document
        doc = Document.objects.filter(pk=document_id, owner=request.user, status=Document.Status.READY).only('pk', 'title').first()
        if doc:
            initial = {'document': doc.pk, 'title': doc.title}
    form = PaperImportForm(request.POST or None, user=request.user, initial=initial)
    if request.method == 'POST' and form.is_valid():
        try:
            kind = form.cleaned_data.get('kind') or 'auto'
            if kind == 'reviewer':
                paper, created = create_reviewer(request.user, form.cleaned_data['document'], form.cleaned_data['title'], form.cleaned_data['topic'], form.cleaned_data.get('question_count') or 5)
            else:
                try:
                    paper, created = import_document(request.user, form.cleaned_data['document'], form.cleaned_data['title'], form.cleaned_data['topic'])
                except PaperImportError as error:
                    if kind != 'auto' or not str(error).startswith('No complete'):
                        raise
                    paper, created = create_reviewer(request.user, form.cleaned_data['document'], form.cleaned_data['title'], form.cleaned_data['topic'], form.cleaned_data.get('question_count') or 5)
        except PaperImportError as error:
            form.add_error('document', str(error))
        else:
            messages.success(request, 'Reviewer generation queued. You can publish when it is ready.' if paper.kind == 'reviewer' and created else 'Question set ready. Publish answered questions together, or use the editor.')
            return redirect('paper_detail', pk=paper.pk)
    papers = ExamPaper.objects.filter(owner=request.user).annotate(question_count=Count('questions'), published_count=Count('questions', filter=Q(questions__reviewed=True)))
    return render(request, 'learning/import_paper.html', {'form': form, 'papers': papers})


@login_required
def paper_detail(request, pk):
    paper = get_object_or_404(ExamPaper.objects.select_related('document'), pk=pk, owner=request.user)
    sort = request.GET.get('sort', 'original')
    if sort not in PAPER_SORTS:
        sort = 'original'
    questions = paper.questions.filter(owner=request.user).order_by(*PAPER_SORTS[sort])
    published_count = questions.filter(reviewed=True).count()
    return render(request, 'learning/paper_detail.html', {
        'paper': paper, 'questions': questions, 'sort': sort,
        'sort_options': [('original', 'Paper order'), ('topic', 'Topic A–Z'), ('drafts', 'Drafts first'), ('published', 'Published first')],
        'published_count': published_count, 'draft_count': questions.count() - published_count, 'answerable_count': questions.filter(reviewed=False, correct_index__isnull=False).count()})


@login_required
def review_question(request, pk):
    question = get_object_or_404(Question.objects.select_related('paper', 'source_document'), pk=pk, owner=request.user, paper__owner=request.user, paper__isnull=False)
    publish = request.POST.get('action') == 'publish'
    form = QuestionReviewForm(request.POST or None, instance=question, publish=publish)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Question reviewed and published to your practice bank.' if publish else 'Question saved as a draft and removed from practice.')
        return redirect('paper_detail', pk=question.paper_id)
    return render(request, 'learning/review_question.html', {'form': form, 'q': question, 'paper': question.paper})


@login_required
@require_POST
def publish_paper(request, pk):
    with transaction.atomic():
        paper = get_object_or_404(ExamPaper.objects.select_for_update(),pk=pk,owner=request.user)
        if paper.generation_status != 'ready':
            messages.info(request,'Wait for generation to finish before publishing.')
        else:
            questions = paper.questions.filter(owner=request.user,reviewed=False,correct_index__isnull=False)
            count = questions.update(reviewed=True)
            missing = paper.questions.filter(owner=request.user,correct_index__isnull=True).count()
            messages.success(request,f'{count} questions published to Practice. {missing} questions without answers remain drafts.')
    return redirect('paper_detail',pk=pk)


@login_required
def reviewer_status(request, pk):
    paper=get_object_or_404(ExamPaper,pk=pk,owner=request.user)
    return JsonResponse({'status':paper.generation_status,'error':paper.generation_error,'can_retry':paper.can_retry_generation})


@login_required
@require_POST
def retry_reviewer(request, pk):
    with transaction.atomic():
        paper=get_object_or_404(ExamPaper.objects.select_for_update(),pk=pk,owner=request.user,kind='reviewer')
        if paper.can_retry_generation:
            try:
                count = int(request.POST.get('question_count') or paper.requested_count)
            except (ValueError,TypeError):
                count = 0
            if not 1 <= count <= 10:
                messages.error(request,'Choose between 1 and 10 reviewer questions.')
            else:
                paper.requested_count = count
                paper.save(update_fields=['requested_count'])
                queue_reviewer(paper)
    return redirect('paper_detail',pk=pk)
