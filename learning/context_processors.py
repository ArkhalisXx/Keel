from django.contrib.staticfiles import finders
from .models import Document


def assistant_workspace(request):
    branding = {'brand_logo': 'images/keel-logo.png' if finders.find('images/keel-logo.png') else ''}
    if not request.user.is_authenticated:
        return branding
    icon = 'images/assistant-boat.png' if finders.find('images/assistant-boat.png') else 'images/assistant-boat.svg'
    docs = list(Document.objects.filter(owner=request.user, status=Document.Status.READY).only('pk', 'title').order_by('title'))
    scope = request.session.get('assistant_scope', [])
    valid = len(scope) == 3 and scope[0] == request.user.pk and scope[1] in ('study', 'documents')
    selected = [pk for pk in scope[2] if pk in {doc.pk for doc in docs}] if valid else []
    history = request.session.get('assistant_history', []) if valid and selected == scope[2] else []
    return {
        **branding,
        'assistant_documents': docs,
        'assistant_icon': icon,
        'assistant_initial_state': {'mode': scope[1] if valid else 'study', 'documents': selected, 'history': history},
    }
