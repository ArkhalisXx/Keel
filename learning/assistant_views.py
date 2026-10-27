"""Authenticated assistant endpoints. Conversation history stays in the user's session."""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from .assistant import AIError, answer_question
from .assistant_forms import AssistantForm
from .services import retrieve
from .models import Document


@login_required
def assistant_page(request):
    form = AssistantForm(user=request.user)
    return render(request, 'learning/assistant.html', {
        'form': form, 'chat_history': request.session.get('assistant_history', []),
        'ready_docs': Document.objects.filter(owner=request.user, status=Document.Status.READY).defer('pages').order_by('title', 'pk'),
    })


@login_required
@require_POST
def assistant_chat(request):
    if request.content_type == 'application/json':
        try:
            data = json.loads(request.body)
            if not isinstance(data, dict):
                raise ValueError()
        except (ValueError, UnicodeError):
            return JsonResponse({'error': 'Send a valid JSON object.', 'code': 'invalid_request', 'search_suggestions_html': ''}, status=400)
    else:
        data = request.POST.copy()
    if data.get('reset') in (True, 'true', '1'):
        request.session.pop('assistant_history', None)
        request.session.pop('assistant_scope', None)
        return JsonResponse({'reset': True, 'search_suggestions_html': ''})
    form = AssistantForm(data, user=request.user)
    if not form.is_valid():
        return JsonResponse({'error': 'Check your question and selected documents.',
                             'errors': form.errors.get_json_data(), 'code': 'validation', 'search_suggestions_html': ''}, status=400)
    mode = form.cleaned_data['mode']
    # Private passages must never enter provider web-search requests.
    documents = list(form.cleaned_data['documents']) if mode == 'documents' else []
    question = form.cleaned_data['question']
    scope = [request.user.pk, mode, sorted(doc.pk for doc in documents)]
    history = request.session.get('assistant_history', []) if request.session.get('assistant_scope') == scope else []
    # Previous questions make follow-up terms such as "explain that formula" useful for retrieval.
    query = question + ' ' + ' '.join(item['content'][:500] for item in history[-4:] if item.get('role') == 'user')
    sources = retrieve(documents, query) if documents else []
    if mode == 'documents' and not sources:
        return JsonResponse({'answer': 'No matching passages were found in those documents. Try a specific topic, another reference, or web search.',
                             'document_sources': [], 'web_sources': [], 'mode': mode, 'web_used': False, 'warning': '', 'search_suggestions_html': ''})
    try:
        result = answer_question(question, mode=mode, document_sources=sources, history=history)
    except AIError as error:
        if error.code == 'missing_key' and mode == 'documents':
            return JsonResponse({'answer': 'AI explanations are not configured yet. Here are the matching passages from your documents; add your provider key privately to enable explanations.',
                                 'document_sources': sources, 'web_sources': [], 'mode': mode,
                                 'web_used': False, 'warning': '', 'search_suggestions_html': '', 'configured': False})
        return JsonResponse({'error': str(error), 'code': error.code,
                             'document_sources': sources, 'search_suggestions_html': ''}, status=503)
    except Exception:
        return JsonResponse({'error': 'The assistant could not complete this request. Please try again.',
                             'code': 'unexpected', 'search_suggestions_html': ''}, status=503)
    request.session['assistant_scope'] = scope
    request.session['assistant_history'] = (history + [
        {'role': 'user', 'content': question},
        {'role': 'assistant', 'content': result['answer'][:6000]},
    ])[-6:]
    return JsonResponse(result)
