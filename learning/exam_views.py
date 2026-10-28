from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from .reviewers import create_reviewer, queue_reviewer
from django.shortcuts import get_object_or_404, redirect, render

from .exam_forms import PaperImportForm, QuestionReviewForm
from .assistant import AIError
from .exams import PaperImportError, import_document
from .models import Document, ExamPaper, Question

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
            scope = {key: form.cleaned_data.get(key) for key in ('focus', 'page_start', 'page_end')}
            scoped = bool(form.cleaned_data.get('focus') or request.POST.get('page_start') or request.POST.get('page_end'))
            if kind == 'reviewer' or (kind == 'auto' and scoped):
                paper, created = create_reviewer(request.user, form.cleaned_data['document'], form.cleaned_data['title'], form.cleaned_data['topic'], form.cleaned_data.get('question_count') or 20, **scope)
            else:
                try:
                    paper, created = import_document(request.user, form.cleaned_data['document'], form.cleaned_data['title'], form.cleaned_data['topic'])
                except PaperImportError as error:
                    if kind != 'auto' or not str(error).startswith('No complete'):
                        raise
                    paper, created = create_reviewer(request.user, form.cleaned_data['document'], form.cleaned_data['title'], form.cleaned_data['topic'], form.cleaned_data.get('question_count') or 20, **scope)
        except (PaperImportError, AIError) as error:
            form.add_error('document', str(error))
        else:
            messages.success(request, 'Reviewer generation queued. You can publish when it is ready.' if paper.kind == 'reviewer' and created else 'Question set ready. Publish answered questions together, or use the editor.')
            return redirect('paper_detail', pk=paper.pk)
    owned = ExamPaper.objects.filter(owner=request.user)
    stats = owned.aggregate(total=Count('pk', distinct=True), question_total=Count('questions'),
                            published=Count('questions', filter=Q(questions__reviewed=True)))
    stats['questions'] = stats.pop('question_total')
    search = request.GET.get('paper_search', '').strip()[:200]
    paper_kind = request.GET.get('paper_kind', '')
    if paper_kind not in ('exam', 'reviewer'):
        paper_kind = ''
    papers = owned.select_related('document').annotate(question_count=Count('questions'), published_count=Count('questions', filter=Q(questions__reviewed=True)))
    if search:
        papers = papers.filter(Q(title__icontains=search) | Q(topic__icontains=search) | Q(document__title__icontains=search))
    if paper_kind:
        papers = papers.filter(kind=paper_kind)
    page = Paginator(papers.order_by('-created_at', '-pk'), 8).get_page(request.GET.get('page'))
    return render(request, 'learning/import_paper.html', {
        'form': form, 'papers': page, 'paper_page': page, 'paper_stats': stats,
        'paper_search': search, 'paper_kind': paper_kind})


@login_required
@require_GET
def paper_source(request, pk):
    """Private document structure only; source text is never exposed by this endpoint."""
    from .reviewer_sources import available_page_bounds, document_outline
    document = get_object_or_404(Document, pk=pk, owner=request.user, status=Document.Status.READY)
    first, last = available_page_bounds(document)
    return JsonResponse({'title': document.title, 'page_min': first, 'page_max': last,
                         'chapters': document_outline(document)})


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
    return JsonResponse({'status':paper.generation_status,'error':paper.generation_error,'can_retry':paper.can_retry_generation,
                         'generated_count':paper.generated_count,'requested_count':paper.requested_count})


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
            minimum = max(1, paper.generated_count)
            if not minimum <= count <= 100:
                messages.error(request,f'Choose between {minimum} and 100 reviewer questions. Completed questions are kept.')
            else:
                paper.requested_count = count
                paper.save(update_fields=['requested_count'])
                queue_reviewer(paper)
    return redirect('paper_detail',pk=pk)
