"""Small, private search results for the workspace's interactive quick finder."""
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_GET

from .models import Document, ExamPaper
from .practice_groups import _base


@login_required
@require_GET
def workspace_search(request):
    query = request.GET.get('q', '').strip()[:200]
    results = []
    if len(query) >= 2:
        documents = Document.objects.filter(owner=request.user, title__icontains=query).only(
            'pk', 'title', 'status').order_by('title', 'pk')[:4]
        for document in documents:
            results.append({
                'title': document.title,
                'detail': f'Reference library · {document.get_status_display()}',
                'kind': 'document',
                'url': reverse('library') + '?' + urlencode({'q': document.title}),
            })
        papers = ExamPaper.objects.filter(owner=request.user).filter(
            Q(title__icontains=query) | Q(topic__icontains=query)).only(
            'pk', 'title', 'topic').order_by('title', 'pk')[:4]
        for paper in papers:
            results.append({
                'title': paper.title, 'detail': f'Question set · {paper.topic}',
                'kind': 'paper', 'url': reverse('paper_detail', args=[paper.pk]),
            })
        # Reuse Practice's complete ownership rules for joined paper/document
        # metadata, rather than trusting a shared-looking legacy question row.
        questions = _base(request.user).filter(
            Q(prompt__icontains=query) | Q(topic__icontains=query)).only(
            'pk', 'prompt', 'topic').order_by('topic', 'pk')[:4]
        for question in questions:
            prompt = question.prompt
            results.append({
                'title': prompt[:120] + ('…' if len(prompt) > 120 else ''),
                'detail': f'Practice question · {question.topic}', 'kind': 'question',
                'url': reverse('question', args=[question.pk]),
            })
    response = JsonResponse({'results': results})
    response['Cache-Control'] = 'private, no-store'
    return response
