"""Explicit, owner-scoped removal of a paper and its practice history."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .models import Attempt, ExamPaper, PracticeRun


def _uses_questions(run, question_ids):
    """Snapshots are JSON, so use a portable comparison on SQLite and Postgres."""
    return any(
        isinstance(item, dict) and type(item.get('id')) is int
        and item['id'] in question_ids
        for items in (run.questions, run.results)
        if isinstance(items, list)
        for item in items
    )


def _impact(paper, user, runs):
    question_ids = set(paper.questions.values_list('pk', flat=True))
    run_ids = [run.pk for run in runs if _uses_questions(run, question_ids)]
    return {
        'question_count': len(question_ids),
        'attempt_count': Attempt.objects.filter(owner=user, question_id__in=question_ids).count(),
        'session_count': len(run_ids),
    }, run_ids


@login_required
@require_http_methods(['GET', 'POST'])
def delete_paper(request, pk):
    if request.method == 'POST' and request.POST.get('confirm_delete') == 'yes':
        with transaction.atomic():
            # Reviewer batches and new practice runs also lock the paper before
            # writing, so neither can recreate its content after this deletion.
            paper = get_object_or_404(
                ExamPaper.objects.select_for_update(), pk=pk, owner=request.user)
            # A flashcard answer locks its run before its original question. Keep
            # that same order to wait for in-flight answers before cascading.
            runs = list(PracticeRun.objects.select_for_update().filter(
                owner=request.user).only('pk', 'questions', 'results').order_by('pk'))
            impact, run_ids = _impact(paper, request.user, runs)
            title = paper.title
            PracticeRun.objects.filter(pk__in=run_ids, owner=request.user).delete()
            paper.delete()
        messages.success(request,
            f'Deleted “{title}”, {impact["question_count"]} questions, '
            f'{impact["attempt_count"]} practice attempts, and '
            f'{impact["session_count"]} flashcard sessions. Analytics have been updated. '
            'The original document remains in Library.')
        return redirect('import_paper')

    paper = get_object_or_404(ExamPaper.objects.select_related('document'),
                             pk=pk, owner=request.user)
    impact, _ = _impact(paper, request.user, PracticeRun.objects.filter(
        owner=request.user).only('pk', 'questions', 'results').iterator(chunk_size=100))
    context = {'paper': paper, **impact}
    if request.method == 'POST':
        context['confirmation_error'] = 'Confirm that you understand the effect on Practice and analytics before deleting.'
    return render(request, 'learning/delete_paper.html', context,
                  status=400 if request.method == 'POST' else 200)
