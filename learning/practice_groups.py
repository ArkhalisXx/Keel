"""Document/set collections and owned, submit-once multi-question practice runs."""
import re
from collections import OrderedDict

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .exams import visible_questions
from .models import Attempt, ExamPaper, PracticeRun, Question

MAX_SESSION_QUESTIONS = 1000
GROUP_SIGNING_SALT = 'keel.practice.group.v1'
SORTS = {'newest', 'oldest', 'topic', 'paper', 'least'}
PROGRESS = {'new', 'review', 'correct'}


def _base(user):
    # Guard joined metadata too: a malformed legacy row must not expose another
    # owner's paper/document title merely because its question has owner=NULL.
    return visible_questions(user).filter(
        Q(paper__isnull=True) | Q(paper__owner=user)
    ).filter(Q(source_document__isnull=True) | Q(source_document__owner=user)).filter(
        Q(paper__document__isnull=True) | Q(paper__document__owner=user))


def _filters(data):
    return {'q': str(data.get('q', '')).strip()[:200],
            'topic': str(data.get('topic', '')).strip()[:120],
            'paper': str(data.get('paper', ''))[:30],
            'progress': data.get('progress', '') if data.get('progress', '') in PROGRESS else '',
            'sort': data.get('sort', 'newest') if data.get('sort', 'newest') in SORTS else 'newest'}


def _questions(user, filters):
    qs = _base(user)
    if filters['topic']:
        qs = qs.filter(topic=filters['topic'])
    if filters['q']:
        qs = qs.filter(Q(prompt__icontains=filters['q']) | Q(topic__icontains=filters['q'])
                       | Q(paper__title__icontains=filters['q'])
                       | Q(source_document__title__icontains=filters['q']))
    if filters['paper']:
        if not filters['paper'].isdigit() or len(filters['paper']) > 18:
            return qs.none()
        qs = qs.filter(paper_id=filters['paper'], paper__owner=user)
    last = Attempt.objects.filter(owner=user, question_id=OuterRef('pk')).order_by('-created_at', '-pk')
    qs = qs.annotate(last_correct=Subquery(last.values('correct')[:1]),
                     practice_count=Count('attempt', filter=Q(attempt__owner=user)))
    if filters['progress'] == 'new':
        qs = qs.filter(last_correct__isnull=True)
    elif filters['progress'] == 'review':
        qs = qs.filter(last_correct=False)
    elif filters['progress'] == 'correct':
        qs = qs.filter(last_correct=True)
    return qs


def _section(source_number):
    match = re.fullmatch(r'(.{1,80})-\d+', source_number or '')
    return match.group(1).strip() if match else ''


def _identity(question):
    section = _section(question.source_number)
    if question.paper_id:
        return {'paper': question.paper_id, 'section': section}
    if question.source_document_id:
        return {'document': question.source_document_id, 'section': section}
    return {'topic': question.topic}


def _title(question, identity):
    if question.paper_id:
        name = question.paper.title
    elif question.source_document_id:
        name = question.source_document.title
    else:
        name = question.topic + ' collection'
    section = identity.get('section', '')
    return f'{name} · Set {section}' if section else name


@login_required
def grouped_library(request):
    filters = _filters(request.GET)
    topics = list(_base(request.user).values_list('topic', flat=True).distinct().order_by('topic'))
    qs = _questions(request.user, filters).select_related('paper__document', 'source_document').defer(
        'prompt', 'choices', 'correct_index', 'explanation', 'reference',
        'paper__document__pages', 'source_document__pages')
    groups = OrderedDict()
    question_count = 0
    for question in qs.order_by('paper_id', 'position', 'pk'):
        identity = _identity(question)
        identity_key = tuple(sorted(identity.items()))
        if identity_key not in groups:
            doc = question.paper.document if question.paper_id and question.paper.document_id else question.source_document
            groups[identity_key] = {
                'key': signing.dumps({'user': request.user.pk, 'identity': identity, 'filters': filters}, salt=GROUP_SIGNING_SALT, compress=True),
                'title': _title(question, identity), 'paper_title': question.paper.title if question.paper_id else '',
                'document_title': doc.title if doc else '', 'section': identity.get('section', ''),
                'count': 0, 'attempted': 0, 'correct': 0, 'accuracy': None,
                'topic': question.topic, 'kind': question.paper.kind if question.paper_id else 'collection',
                'paper_id': question.paper_id, '_newest': question.pk, '_oldest': question.pk, '_attempts': 0,
                '_collection_order': question.paper_id or question.source_document_id or 0,
            }
        group = groups[identity_key]
        question_count += 1
        group['count'] += 1
        group['attempted'] += question.last_correct is not None
        group['correct'] += question.last_correct is True
        group['_newest'] = max(group['_newest'], question.pk)
        group['_oldest'] = min(group['_oldest'], question.pk)
        group['_attempts'] += question.practice_count
    result = list(groups.values())
    for group in result:
        if group['attempted']:
            group['accuracy'] = round(100 * group['correct'] / group['attempted'])
    def section_order(group):
        return tuple((0, int(piece)) if piece.isdigit() else (1, piece.casefold())
                     for piece in re.split(r'(\d+)', group['section']) if piece)
    if filters['sort'] == 'newest':
        result.sort(key=lambda group: (-group['_collection_order'], section_order(group), -group['_newest']))
    elif filters['sort'] == 'oldest':
        result.sort(key=lambda group: (group['_collection_order'], section_order(group), group['_oldest']))
    elif filters['sort'] == 'topic':
        result.sort(key=lambda group: (group['topic'].casefold(), group['paper_title'].casefold(), section_order(group)))
    elif filters['sort'] == 'least':
        result.sort(key=lambda group: (group['_attempts'], group['paper_title'].casefold(), section_order(group)))
    else:
        result.sort(key=lambda group: (group['paper_title'].casefold(), section_order(group), group['_oldest']))
    return render(request, 'learning/practice.html', {
        'groups': Paginator(result, 12).get_page(request.GET.get('page')),
        'question_count': question_count, 'topics': topics,
        'papers': ExamPaper.objects.filter(owner=request.user).order_by('title', 'pk'),
        'search': filters['q'], 'topic': filters['topic'], 'paper_id': filters['paper'],
        'progress': filters['progress'], 'sort': filters['sort'], 'filters': filters,
    })


@login_required
@require_POST
def start_practice(request):
    try:
        data = signing.loads(request.POST.get('group', ''), salt=GROUP_SIGNING_SALT, max_age=86400)
        if not isinstance(data, dict) or data.get('user') != request.user.pk or not isinstance(data.get('identity'), dict):
            raise signing.BadSignature()
        identity = data['identity']
        filters = _filters(data.get('filters', {}))
    except (signing.BadSignature, ValueError, TypeError):
        return HttpResponseBadRequest('This practice link is invalid or expired. Open Practice and select the group again.')
    # The signed descriptor is only a selector. Access is always rechecked now.
    candidates = _questions(request.user, filters).select_related('paper', 'source_document').order_by('position', 'pk')
    if 'paper' in identity:
        candidates = candidates.filter(paper_id=identity['paper'])
    elif 'document' in identity:
        candidates = candidates.filter(paper__isnull=True, source_document_id=identity['document'])
    elif 'topic' in identity:
        candidates = candidates.filter(paper__isnull=True, source_document__isnull=True, topic=identity['topic'])
    else:
        return HttpResponseBadRequest('Invalid collection.')
    selected = []
    for question in candidates.iterator(chunk_size=200):
        if _identity(question) == identity:
            selected.append(question)
            if len(selected) > MAX_SESSION_QUESTIONS:
                messages.error(request, 'This collection exceeds 1,000 questions. Use topic or progress filters to narrow it down.')
                return redirect('practice')
    if not selected:
        messages.info(request, 'No published questions remain in this selection. Choose another collection.')
        return redirect('practice')
    snapshots = [{'id': q.pk, 'number': q.source_number, 'prompt': q.prompt, 'choices': q.choices,
                  'correct_index': q.correct_index, 'explanation': q.explanation, 'reference': q.reference,
                  'source_document_id': q.source_document_id or (q.paper.document_id if q.paper_id else None),
                  'ai_generated': bool(q.paper_id and q.paper.kind == 'reviewer')} for q in selected]
    run = PracticeRun.objects.create(owner=request.user, title=_title(selected[0], identity)[:300], questions=snapshots)
    return redirect('practice_run', pk=run.pk)


def _summary(run):
    return {'id': run.pk, 'title': run.title, 'completed': run.completed, 'score': run.score if run.completed else None,
            'answered': len(run.results), 'total': len(run.questions),
            'percent': round(100 * run.score / len(run.questions)) if run.completed and run.questions else None}


def _active(run):
    if run.results and not run.results[-1].get('advanced', True):
        return len(run.results) - 1, True
    return len(run.results), False


def _public_item(item, index):
    return {'id': item['id'], 'index': index, 'number': item.get('number', ''),
            'prompt': item['prompt'], 'choices': item['choices'],
            'source_document_id': item.get('source_document_id'),
            'ai_generated': item.get('ai_generated', False), 'selected_index': None}


@login_required
def practice_run(request, pk):
    run = get_object_or_404(PracticeRun, pk=pk, owner=request.user)
    errors = []
    if request.method == 'POST':
        with transaction.atomic():
            run = get_object_or_404(PracticeRun.objects.select_for_update(), pk=pk, owner=request.user)
            if run.completed:
                return redirect('practice_run', pk=pk)
            cursor, feedback = _active(run)
            card = request.POST.get('card', '')
            action = request.POST.get('action', '')
            if not card.isdigit() or len(card) > 5:
                errors.append('Select a valid card from this session.')
            elif int(card) != cursor:
                # A duplicate browser submission after automatic advancement is
                # harmless; it never answers or skips the next question.
                if action == 'answer' and int(card) < len(run.results):
                    return redirect('practice_run', pk=pk)
                errors.append('This card is no longer active. Continue from the current card.')
            elif action == 'next' and feedback:
                results = [dict(item) for item in run.results]
                results[-1]['advanced'] = True
                PracticeRun.objects.filter(pk=pk, owner=request.user, completed=False, results=run.results).update(
                    results=results, completed=len(results) == len(run.questions),
                    completed_at=timezone.now() if len(results) == len(run.questions) else None)
                return redirect('practice_run', pk=pk)
            elif action == 'answer' and feedback:
                return redirect('practice_run', pk=pk)
            elif action == 'answer' and cursor < len(run.questions):
                values = request.POST.getlist('answer')
                if len(values) != 1 or values[0] not in ('0', '1', '2', '3'):
                    errors.append('Choose one answer before continuing.')
                else:
                    item = run.questions[cursor]
                    choice = int(values[0])
                    result = {**item, 'index': cursor, 'selected_index': choice,
                              'correct': choice == item['correct_index'],
                              'advanced': request.POST.get('reveal') != 'yes'}
                    results = run.results + [result]
                    done = len(results) == len(run.questions) and result['advanced']
                    updated = PracticeRun.objects.filter(pk=pk, owner=request.user, completed=False, results=run.results).update(
                        results=results, score=sum(value['correct'] for value in results), completed=done,
                        completed_at=timezone.now() if done else None)
                    original = _base(request.user).select_for_update(of=('self',)).filter(pk=item['id']).first() if updated else None
                    if original is not None:
                        Attempt.objects.create(owner=request.user, question_id=item['id'],
                            selected_index=choice, correct=result['correct'])
                    return redirect('practice_run', pk=pk)
            else:
                errors.append('Choose an answer or continue to the next card.')
    if run.completed:
        return render(request, 'learning/practice_run.html', {
            'run': _summary(run), 'items': run.results, 'item': None, 'feedback': False, 'errors': []})
    cursor, feedback = _active(run)
    if cursor >= len(run.questions):
        return HttpResponseBadRequest('This practice session has no remaining cards.')
    item = dict(run.results[-1]) if feedback else _public_item(run.questions[cursor], cursor)
    # The only answer-bearing item exposed before completion is a card that the
    # owner already answered and explicitly chose to reveal.
    return render(request, 'learning/practice_run.html', {
        'run': _summary(run), 'item': item, 'feedback': feedback, 'errors': errors,
    })
