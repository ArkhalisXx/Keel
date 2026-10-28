import json
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase,override_settings
from django.utils import timezone
from datetime import timedelta
from .models import Document,ExamPaper,Question
from .reviewers import create_reviewer,generate_reviewer,generate_items,queue_reviewer
from .assistant import AIError

ITEM={'prompt':'What supports a ship?', 'choices':['Buoyancy','Drag','Thrust','Friction'],'correct_index':0,'explanation':'The source states that buoyancy supports a ship.','source_page':1}

@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ReviewerTests(TestCase):
 def setUp(self):
  self.user=User.objects.create_user('reviewer');self.other=User.objects.create_user('other')
  self.client.force_login(self.user)
  self.doc=Document.objects.create(owner=self.user,title='Notes',file='x.txt',status='ready',pages=[{'page':1,'text':'Buoyancy supports a ship.'}])
 def paper(self,**kwargs):
  fields={'owner':self.user,'document':self.doc,'title':'Reviewer','topic':'Stability','kind':'reviewer','requested_count':1}
  fields.update(kwargs)
  return ExamPaper.objects.create(**fields)
 def test_bulk_publish_answered_questions_without_manual_explanations(self):
  p=self.paper();q=Question.objects.create(owner=self.user,paper=p,topic='Topic',prompt='Question?',choices=['a','b','c','d'],correct_index=0,explanation='')
  missing=Question.objects.create(owner=self.user,paper=p,topic='Topic',prompt='Unknown?',choices=['a','b','c','d'])
  self.assertEqual(self.client.get(f'/papers/{p.pk}/publish/').status_code,405)
  self.client.post(f'/papers/{p.pk}/publish/')
  q.refresh_from_db();missing.refresh_from_db();self.assertTrue(q.reviewed);self.assertFalse(missing.reviewed)
  self.assertContains(self.client.get(f'/practice/{q.pk}/'),'Question?')
  self.client.post(f'/papers/{p.pk}/publish/');self.assertEqual(p.questions.filter(reviewed=True).count(),1)
 def test_owner_access_and_processing_cannot_publish(self):
  p=self.paper(generation_status='processing')
  q=Question.objects.create(owner=self.user,paper=p,topic='Topic',prompt='Question?',choices=['a','b','c','d'],correct_index=0)
  self.client.post(f'/papers/{p.pk}/publish/');q.refresh_from_db();self.assertFalse(q.reviewed)
  self.client.force_login(self.other)
  for path in ['publish','retry-generation']:
   self.assertEqual(self.client.post(f'/papers/{p.pk}/{path}/').status_code,404)
  self.assertEqual(self.client.get(f'/papers/{p.pk}/generation/').status_code,404)
 @patch('learning.reviewers.generate_reviewer.delay')
 def test_creation_is_queued_after_commit_and_supports_multiple_reviewer_versions(self,publish):
  with self.captureOnCommitCallbacks(execute=True):
   p,created=create_reviewer(self.user,self.doc,'Reviewer','Topic',1)
   self.assertTrue(created);publish.assert_not_called()
  self.assertEqual(publish.call_count,1)
  with self.captureOnCommitCallbacks(execute=True):
   another,created=create_reviewer(self.user,self.doc,'Another','Topic',1)
  self.assertTrue(created);self.assertNotEqual(p.pk,another.pk)
  self.assertEqual(publish.call_count,2)
 @patch('learning.reviewers.answer_question',return_value={'answer':json.dumps([ITEM])})
 def test_generation_drafts_explanations_provenance_and_duplicate_delivery(self,answer):
  p=self.paper(generation_status='queued')
  generate_reviewer.run(p.pk,str(p.generation_token));p.refresh_from_db()
  self.assertEqual(p.generation_status,'ready');self.assertEqual(p.generated_count,1);q=p.questions.get();self.assertFalse(q.reviewed)
  self.assertEqual(q.explanation,ITEM['explanation']);self.assertEqual(q.correct_index,0)
  self.assertIn('AI-generated',q.reference)
  generate_reviewer.run(p.pk,str(p.generation_token));self.assertEqual(answer.call_count,1)
 def test_invalid_answers_and_invented_page_rejected(self):
  for item in [dict(ITEM,correct_index=7),dict(ITEM,source_page=99),dict(ITEM,explanation=''),dict(ITEM,choices=['one'])]:
   with patch('learning.reviewers.answer_question',return_value={'answer':json.dumps([item])}):
    with self.assertRaises(AIError):generate_items(self.doc,1)
 @patch('learning.reviewers.answer_question',side_effect=AIError('Quota reached','http_429'))
 def test_api_failure_and_retry(self,answer):
  p=self.paper(generation_status='queued');old=p.generation_token
  generate_reviewer.run(p.pk,str(old));p.refresh_from_db()
  self.assertEqual(p.generation_status,'failed');self.assertEqual(p.generation_error,'Quota reached');self.assertEqual(p.questions.count(),0)
  with patch('learning.reviewers.generate_reviewer.delay'),self.captureOnCommitCallbacks(execute=True):
   self.client.post(f'/papers/{p.pk}/retry-generation/')
  p.refresh_from_db();self.assertNotEqual(p.generation_token,old)
 def test_stalled_generation_can_restart(self):
  p=self.paper(generation_status='processing',generation_deadline=timezone.now()-timedelta(seconds=1))
  self.assertTrue(p.can_retry_generation)
  with patch('learning.reviewers.generate_reviewer.delay'),self.captureOnCommitCallbacks(execute=True):
   self.client.post(f'/papers/{p.pk}/retry-generation/')
  p.refresh_from_db();self.assertEqual(p.generation_status,'queued');self.assertFalse(p.can_retry_generation)
 def test_reviewer_form_renders_and_no_explanation_required_in_editor(self):
  p=self.paper();q=Question.objects.create(owner=self.user,paper=p,topic='Topic',prompt='Question?',choices=['a','b','c','d'],correct_index=0)
  self.assertContains(self.client.get(f'/papers/{p.pk}/'),'Publish answered questions')
  self.assertContains(self.client.get('/papers/import/'),'Generate a Q&amp;A reviewer')
  from .exam_forms import QuestionReviewForm
  f=QuestionReviewForm({'topic':'Topic','prompt':'Question?','choice_a':'a','choice_b':'b','choice_c':'c','choice_d':'d','correct_index':'0','explanation':'','reference':''},instance=q,publish=True)
  self.assertTrue(f.is_valid(),f.errors)
 @patch('learning.reviewers.generate_reviewer.delay')
 def test_auto_mode_generates_from_notes_without_numbered_questions(self,publish):
  with self.captureOnCommitCallbacks(execute=True):
   response=self.client.post('/papers/import/',{'kind':'auto','title':'My reviewer','topic':'Stability','document':self.doc.pk,'question_count':'1'})
  self.assertEqual(response.status_code,302)
  paper=ExamPaper.objects.get(owner=self.user)
  self.assertEqual(paper.kind,'reviewer');self.assertEqual(paper.generation_status,'queued')
  publish.assert_called_once()

 def test_larger_reviewer_runs_in_small_batches_and_records_progress(self):
  p=self.paper(requested_count=12,generation_status='queued')
  def batch(document,count,**kwargs):
   return [dict(ITEM,prompt=f'Question {kwargs["offset"]+i}?') for i in range(count)]
  with patch('learning.reviewers.generate_items',side_effect=batch) as generate,patch('learning.reviewers.generate_reviewer.delay') as dispatch:
   with self.captureOnCommitCallbacks(execute=True):
    generate_reviewer.run(p.pk,str(p.generation_token))
   p.refresh_from_db();self.assertEqual(p.generated_count,5);self.assertEqual(p.generation_status,'queued')
   dispatch.assert_called_once()
   with self.captureOnCommitCallbacks(execute=True):
    generate_reviewer.run(p.pk,str(p.generation_token))
   p.refresh_from_db();self.assertEqual(p.generated_count,10)
   with self.captureOnCommitCallbacks(execute=True):
    generate_reviewer.run(p.pk,str(p.generation_token))
   p.refresh_from_db();self.assertEqual(p.generated_count,12);self.assertEqual(p.generation_status,'ready')
   self.assertEqual([call.args[1] for call in generate.call_args_list],[5,5,2])
   self.assertEqual(p.questions.count(),12)
   generate_reviewer.run(p.pk,str(p.generation_token));self.assertEqual(generate.call_count,3)
   self.assertEqual(list(p.questions.order_by('position').values_list('position',flat=True)),list(range(1,13)))

 def test_failed_batch_keeps_previous_questions_and_retry_resumes(self):
  p=self.paper(requested_count=7,generation_status='queued')
  first=[dict(ITEM,prompt=f'Saved question {i}?') for i in range(5)]
  with patch('learning.reviewers.generate_items',side_effect=[first,AIError('Quota reached','http_429')]),patch('learning.reviewers.generate_reviewer.delay'):
   with self.captureOnCommitCallbacks(execute=True):generate_reviewer.run(p.pk,str(p.generation_token))
   generate_reviewer.run(p.pk,str(p.generation_token))
  p.refresh_from_db();self.assertEqual(p.generation_status,'failed');self.assertEqual(p.generated_count,5)
  oldtoken=p.generation_token
  saved=list(p.questions.values_list('pk',flat=True))
  with patch('learning.reviewers.generate_reviewer.delay'),self.captureOnCommitCallbacks(execute=True):queue_reviewer(p)
  p.refresh_from_db();self.assertNotEqual(p.generation_token,oldtoken)
  with patch('learning.reviewers.generate_items',return_value=[dict(ITEM,prompt='New question 6?'),dict(ITEM,prompt='New question 7?')]) as generate:
   generate_reviewer.run(p.pk,str(oldtoken));generate.assert_not_called()
   generate_reviewer.run(p.pk,str(p.generation_token))
  p.refresh_from_db();self.assertEqual(p.generated_count,7);self.assertEqual(p.generation_status,'ready')
  self.assertEqual(set(saved),set(p.questions.filter(pk__in=saved).values_list('pk',flat=True)))
  self.assertEqual(generate.call_args.args[1],2)
  self.assertEqual(generate.call_args.kwargs['offset'],5)

 def test_repeated_questions_and_short_batches_are_rejected(self):
  for items,previous in [([ITEM,ITEM],[]),([ITEM],[]),([ITEM], [ITEM['prompt'].upper()])]:
   count=1 if previous else 2
   with patch('learning.reviewers.answer_question',return_value={'answer':json.dumps(items)}):
    with self.assertRaises(AIError):generate_items(self.doc,count,previous_prompts=previous)

 def test_stale_or_deleted_paper_cannot_receive_a_completed_batch(self):
  import uuid
  for delete in (False,True):
   p=self.paper(generation_status='queued')
   def finish(*args,**kwargs):
    if delete:p.delete()
    else:ExamPaper.objects.filter(pk=p.pk).update(generation_token=uuid.uuid4())
    return [ITEM]
   with patch('learning.reviewers.generate_items',side_effect=finish):generate_reviewer.run(p.pk,str(p.generation_token))
   self.assertFalse(Question.objects.exists())

 def test_completed_progress_survives_unavailable_queue(self):
  p=self.paper(requested_count=2,generation_status='failed')
  Question.objects.create(owner=self.user,paper=p,topic='Topic',prompt=ITEM['prompt'],choices=ITEM['choices'],correct_index=0)
  with patch('learning.reviewers.generate_reviewer.delay',side_effect=OSError('private internal error')),self.captureOnCommitCallbacks(execute=True):queue_reviewer(p)
  p.refresh_from_db();self.assertEqual(p.generated_count,1);self.assertEqual(p.generation_status,'failed')
  self.assertNotIn('private',p.generation_error)

 def test_scope_and_count_are_validated_before_queueing(self):
  from .exams import PaperImportError
  for count in (0,101,True):
   with self.assertRaises(PaperImportError):create_reviewer(self.user,self.doc,'Reviewer','Topic',count)
  for kwargs in ({'page_start':2},{'page_start':1,'page_end':0},{'focus':'Unrelated thermodynamics'}):
   with self.assertRaises(PaperImportError):create_reviewer(self.user,self.doc,'Reviewer','Topic',1,**kwargs)
  with self.assertRaises(PaperImportError):create_reviewer(self.other,self.doc,'Reviewer','Topic',1)
  self.assertFalse(ExamPaper.objects.exists())

 @patch('learning.reviewers.generate_reviewer.delay')
 def test_selected_scope_is_saved_and_sent_to_generator(self,dispatch):
  with self.captureOnCommitCallbacks(execute=True):
   p,_=create_reviewer(self.user,self.doc,'Scoped reviewer','Stability',50,focus='Buoyancy',page_start=1,page_end=1)
  self.assertEqual(p.requested_count,50);self.assertEqual(p.generation_focus,'Buoyancy')
  self.assertEqual((p.source_page_start,p.source_page_end),(1,1))
  with patch('learning.reviewers.generate_items',return_value=[dict(ITEM,prompt=f'Question {i}?') for i in range(5)]) as generate:
   generate_reviewer.run(p.pk,str(ExamPaper.objects.get(pk=p.pk).generation_token))
  self.assertEqual(generate.call_args.kwargs['focus'],'Buoyancy')
  self.assertEqual(generate.call_args.kwargs['page_start'],1)

 def test_hundred_question_reviewer_completes_twenty_validated_batches(self):
  import re
  p=self.paper(requested_count=100,generation_status='queued')
  calls=[]
  def response(instruction,**kwargs):
   count=int(re.search(r'Create exactly (\d+)',instruction).group(1))
   calls.append(count)
   return {'answer':json.dumps([dict(ITEM,prompt=f'Validated batch {len(calls)} question {i}?') for i in range(count)])}
  with patch('learning.reviewers.answer_question',side_effect=response) as provider,patch('learning.reviewers.generate_reviewer.delay') as dispatch:
   for expected in range(5,101,5):
    with self.captureOnCommitCallbacks(execute=True):generate_reviewer.run(p.pk,str(p.generation_token))
    p.refresh_from_db()
    self.assertEqual(p.generated_count,expected)
    self.assertEqual(p.questions.count(),expected)
    self.assertEqual(p.generation_status,'ready' if expected==100 else 'queued')
   self.assertEqual(provider.call_count,20);self.assertEqual(dispatch.call_count,19)
   generate_reviewer.run(p.pk,str(p.generation_token));self.assertEqual(provider.call_count,20)
  self.assertEqual(calls,[5]*20)
  self.assertEqual(list(p.questions.order_by('position').values_list('position',flat=True)),list(range(1,101)))
