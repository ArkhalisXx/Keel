"""Generate a private reviewer in the existing worker. No generated answer is called verified."""
import json
import re
import uuid
from datetime import timedelta
from django.utils import timezone

from celery import shared_task
from django.db import transaction
from .assistant import AIError, answer_question
from .models import Document, ExamPaper, Question


def queue_reviewer(paper):
    token = uuid.uuid4()
    ExamPaper.objects.filter(pk=paper.pk).update(generation_status='queued', generation_token=token, generation_error='', generation_deadline=timezone.now()+timedelta(minutes=3))
    def dispatch():
        try:
            generate_reviewer.delay(paper.pk, str(token))
        except Exception:
            ExamPaper.objects.filter(pk=paper.pk, generation_token=token, generation_status='queued').update(
                generation_status='failed', generation_error='The processing queue is unavailable. Start Redis and the worker, then retry.')
    transaction.on_commit(dispatch)


def create_reviewer(owner, document, title, topic, count):
    with transaction.atomic():
        doc = Document.objects.select_for_update().get(pk=document.pk, owner=owner, status=Document.Status.READY)
        paper, created = ExamPaper.objects.get_or_create(owner=owner, document=doc, kind='reviewer', defaults={
            'title': title, 'topic': topic, 'requested_count': count, 'generation_status':'queued',
            'warnings':['AI-generated questions and answers may be incorrect. Check formulas and references. The reviewer uses excerpts sampled across the document, not every page.']})
        if created:
            queue_reviewer(paper)
    return paper, created


def generate_items(document, count):
    pages = [page for page in document.pages if page.get('text','').strip()]
    if not pages:
        raise AIError('No readable source text is available. Process the document again.', 'no_text')
    indices = sorted({round(i*(len(pages)-1)/max(1,min(6,len(pages))-1)) for i in range(min(6,len(pages)))})
    sources = [{'title':document.title, 'id':document.pk, 'page':pages[i]['page'], 'text':pages[i]['text'][:2400], 'source':pages[i].get('source','text')} for i in indices]
    instruction = (f'Create exactly {count} useful multiple-choice reviewer questions from these passages. '
        'Return ONLY a JSON array, without markdown. Each item must have prompt (string), choices '
        '(four nonempty strings), correct_index (integer 0–3), explanation (a concise source-grounded explanation), '
        'and source_page (integer from the supplied passages). Do not invent facts or source pages. '
        'Make the explanation self-contained. If the passages are insufficient, return an empty array.')
    result = answer_question(instruction, mode='documents', document_sources=sources)
    text = result['answer'].strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.I).strip()
    try:
        items = json.loads(text)
    except (ValueError, TypeError):
        raise AIError('AI returned an incomplete reviewer. Retry with fewer questions.', 'reviewer_format') from None
    if not isinstance(items, list) or len(items) != count:
        raise AIError('AI did not return the requested number of questions. Retry with fewer questions.', 'reviewer_format')
    allowed_pages = {s['page'] for s in sources}
    for item in items:
        if not isinstance(item,dict) or not isinstance(item.get('prompt'),str) or not item['prompt'].strip() or len(item['prompt'])>5000:
            raise AIError('AI returned an invalid question. Retry generation.', 'reviewer_format')
        choices=item.get('choices')
        if not isinstance(choices,list) or len(choices)!=4 or not all(isinstance(v,str) and v.strip() and len(v)<=2000 for v in choices):
            raise AIError('AI returned invalid answer choices. Retry generation.', 'reviewer_format')
        if type(item.get('correct_index')) is not int or item['correct_index'] not in range(4) or type(item.get('source_page')) is not int or item['source_page'] not in allowed_pages:
            raise AIError('AI returned an invalid answer or page reference. Retry generation.', 'reviewer_format')
        if not isinstance(item.get('explanation'),str) or not item['explanation'].strip() or len(item['explanation'])>8000:
            raise AIError('AI did not explain its answers. Retry generation.', 'reviewer_format')
    return items


@shared_task(ignore_result=True, soft_time_limit=90, time_limit=120)
def generate_reviewer(pk, token):
    current = ExamPaper.objects.filter(pk=pk, generation_token=token, kind='reviewer')
    if not current.filter(generation_status='queued').update(generation_status='processing'):
        return
    try:
        paper = current.select_related('document').first()
        if not paper or not paper.document or paper.document.status != Document.Status.READY:
            raise AIError('The source document is no longer ready. Process it again before retrying.', 'source_missing')
        items = generate_items(paper.document, paper.requested_count)
        with transaction.atomic():
            locked = current.select_for_update().first()
            if not locked or locked.generation_status != 'processing':
                return
            Question.objects.bulk_create([Question(owner=paper.owner,paper=paper,source_document=paper.document,topic=paper.topic,
                prompt=item['prompt'],choices=item['choices'],correct_index=item['correct_index'],explanation=item['explanation'],
                source_page=item['source_page'],source_number=str(i),position=i,reviewed=False,
                reference=f'{paper.document.title}, page/section {item["source_page"]} (AI-generated)'[:300]) for i,item in enumerate(items,1)])
            current.update(generation_status='ready',generation_error='')
    except Exception as error:
        message = str(error) if isinstance(error,AIError) else 'Reviewer generation failed. Check the worker, then retry.'
        current.update(generation_status='failed',generation_error=message)
