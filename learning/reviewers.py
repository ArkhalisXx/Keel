"""Build private, source-scoped reviewers in small, resumable worker batches."""
import json
import re
import uuid
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .assistant import AIError, answer_question
from .exams import PaperImportError
from .models import Document, ExamPaper, Question
from .reviewer_sources import select_sources, validate_scope

MAX_REVIEWER_QUESTIONS = 100
REVIEWER_BATCH_SIZE = 5


def _dispatch_batch(pk, token):
    if settings.JOB_BACKEND == 'database':
        return
    try:
        generate_reviewer.delay(pk, str(token))
    except Exception:
        ExamPaper.objects.filter(pk=pk, generation_token=token, generation_status='queued').update(
            generation_status='failed',
            generation_error='The processing queue is unavailable. Start Redis and the worker, then retry. Completed questions are saved.')


def queue_reviewer(paper):
    """Start a new attempt, keeping questions from successful earlier batches."""
    token = uuid.uuid4()
    completed = paper.questions.count()
    state = 'ready' if completed >= paper.requested_count else 'queued'
    ExamPaper.objects.filter(pk=paper.pk).update(
        generation_status=state, generation_token=token, generation_error='',
        generated_count=completed,
        generation_deadline=timezone.now() + timedelta(minutes=3) if state == 'queued' else None)
    if state == 'queued':
        transaction.on_commit(lambda: _dispatch_batch(paper.pk, token))


def create_reviewer(owner, document, title, topic, count, *, focus='', page_start=None, page_end=None):
    if type(count) is not int or not 1 <= count <= MAX_REVIEWER_QUESTIONS:
        raise PaperImportError(f'Choose between 1 and {MAX_REVIEWER_QUESTIONS} reviewer questions.')
    with transaction.atomic():
        try:
            doc = Document.objects.select_for_update().get(pk=document.pk, owner=owner, status=Document.Status.READY)
        except Document.DoesNotExist:
            raise PaperImportError('Choose a ready document from your own library.') from None
        try:
            focus, page_start, page_end = validate_scope(doc, focus=focus, page_start=page_start, page_end=page_end)
            # Fail before queuing a chargeable request when the requested topic has no matches.
            select_sources(doc, focus=focus, page_start=page_start, page_end=page_end)
        except AIError as error:
            raise PaperImportError(str(error)) from None
        # Reviewer versions may cover different chapters or topics in the same book.
        paper = ExamPaper.objects.create(owner=owner, document=doc, kind='reviewer',
            title=title, topic=topic, requested_count=count, generation_status='queued',
            generation_focus=focus, source_page_start=page_start, source_page_end=page_end,
            warnings=['AI-generated questions and answers may be incorrect. Check formulas and references. '
                      'Questions use excerpts from the selected scope; coverage is not exhaustive.'])
        queue_reviewer(paper)
    return paper, True


def _prompt_key(value):
    return re.sub(r'\W+', '', value.casefold())


def generate_items(document, count, *, focus='', page_start=None, page_end=None, offset=0, previous_prompts=None):
    if type(count) is not int or not 1 <= count <= REVIEWER_BATCH_SIZE:
        raise AIError('The reviewer batch size is invalid. Retry generation.', 'reviewer_count')
    sources = select_sources(document, focus=focus, page_start=page_start, page_end=page_end, offset=offset)
    instruction = (f'Create exactly {count} useful, distinct multiple-choice reviewer questions from these passages. '
        'Return ONLY a JSON array, without markdown. Each item must have prompt (string), choices '
        '(four distinct nonempty strings), correct_index (integer 0–3), explanation '
        '(a concise source-grounded explanation), and source_page (integer from the supplied passages). '
        'Do not invent facts or source pages. Make each explanation self-contained. '
        'If the passages are insufficient, return an empty array. ')
    if focus:
        instruction += f'Limit all questions to this requested topic or chapter: {focus}. '
    previous = list(previous_prompts or [])
    if previous:
        # Bound the prompt while telling the provider about every existing card in this ≤100-card set.
        instruction += '\nDo not repeat or rephrase these existing questions:\n' + '\n'.join(
            f'- {prompt[:200]}' for prompt in previous[-MAX_REVIEWER_QUESTIONS:])
    result = answer_question(instruction, mode='documents', document_sources=sources)
    text = result['answer'].strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.I).strip()
    try:
        items = json.loads(text)
    except (ValueError, TypeError):
        raise AIError('AI returned an incomplete batch. Retry to resume from the saved questions.', 'reviewer_format') from None
    if not isinstance(items, list) or len(items) != count:
        raise AIError('AI could not return enough questions for this scope. Try a broader scope or fewer questions; completed batches are saved.', 'reviewer_format')
    allowed_pages = {source['page'] for source in sources}
    seen = {_prompt_key(prompt) for prompt in previous}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('prompt'), str) or not item['prompt'].strip() or len(item['prompt']) > 5000:
            raise AIError('AI returned an invalid question. Retry generation.', 'reviewer_format')
        key = _prompt_key(item['prompt'])
        if not key or key in seen:
            raise AIError('AI repeated a question. Retry generation; completed batches are saved.', 'reviewer_format')
        seen.add(key)
        choices = item.get('choices')
        if (not isinstance(choices, list) or len(choices) != 4
                or not all(isinstance(value, str) and value.strip() and len(value) <= 2000 for value in choices)
                or len({value.strip().casefold() for value in choices}) != 4):
            raise AIError('AI returned invalid answer choices. Retry generation.', 'reviewer_format')
        if (type(item.get('correct_index')) is not int or item['correct_index'] not in range(4)
                or type(item.get('source_page')) is not int or item['source_page'] not in allowed_pages):
            raise AIError('AI returned an invalid answer or page reference. Retry generation.', 'reviewer_format')
        if not isinstance(item.get('explanation'), str) or not item['explanation'].strip() or len(item['explanation']) > 8000:
            raise AIError('AI did not explain its answers. Retry generation.', 'reviewer_format')
    return items


@shared_task(ignore_result=True, soft_time_limit=90, time_limit=120)
def generate_reviewer(pk, token):
    """Claim one batch; a stale task cannot write after retry or paper deletion."""
    current = ExamPaper.objects.filter(pk=pk, generation_token=token, kind='reviewer')
    if not current.filter(generation_status='queued').update(
            generation_status='processing', generation_deadline=timezone.now() + timedelta(minutes=3)):
        return
    try:
        paper = current.select_related('document').first()
        if (not paper or not paper.document or paper.document.owner_id != paper.owner_id
                or paper.document.status != Document.Status.READY):
            raise AIError('The source document is no longer ready. Process it again before retrying.', 'source_missing')
        if not 1 <= paper.requested_count <= MAX_REVIEWER_QUESTIONS:
            raise AIError(f'Choose between 1 and {MAX_REVIEWER_QUESTIONS} reviewer questions.', 'reviewer_count')
        previous = list(paper.questions.order_by('position', 'pk').values_list('prompt', flat=True))
        remaining = paper.requested_count - len(previous)
        if remaining <= 0:
            current.filter(generation_status='processing').update(
                generation_status='ready', generated_count=len(previous), generation_error='', generation_deadline=None)
            return
        items = generate_items(paper.document, min(REVIEWER_BATCH_SIZE, remaining),
            focus=paper.generation_focus, page_start=paper.source_page_start, page_end=paper.source_page_end,
            offset=len(previous), previous_prompts=previous)
        with transaction.atomic():
            locked = current.select_for_update().first()
            if not locked or locked.generation_status != 'processing':
                return
            # Only this claimed batch can add cards. Completed earlier batches remain untouched.
            completed = locked.questions.count()
            if completed != len(previous):
                raise AIError('The reviewer changed during generation. Retry to resume safely.', 'reviewer_changed')
            Question.objects.bulk_create([Question(owner=paper.owner, paper=paper, source_document=paper.document,
                topic=paper.topic, prompt=item['prompt'].strip(), choices=[value.strip() for value in item['choices']],
                correct_index=item['correct_index'], explanation=item['explanation'].strip(),
                source_page=item['source_page'], source_number=str(i), position=i, reviewed=False,
                reference=f'{paper.document.title}, page/section {item["source_page"]} (AI-generated)'[:300])
                for i, item in enumerate(items, completed + 1)])
            completed += len(items)
            state = 'ready' if completed == locked.requested_count else 'queued'
            current.update(generation_status=state, generated_count=completed, generation_error='',
                generation_deadline=None if state == 'ready' else timezone.now() + timedelta(minutes=3))
            if state == 'queued':
                transaction.on_commit(lambda: _dispatch_batch(pk, token))
    except Exception as error:
        message = str(error) if isinstance(error, AIError) else 'Reviewer generation failed. Check the worker, then retry. Completed batches are saved.'
        current.filter(generation_status='processing').update(
            generation_status='failed', generation_error=message, generation_deadline=None)
