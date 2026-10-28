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
 def paper(self,**kwargs):return ExamPaper.objects.create(owner=self.user,document=self.doc,title='Reviewer',topic='Stability',kind='reviewer',requested_count=1,**kwargs)
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
 def test_creation_is_queued_after_commit_and_duplicate_is_not_charged_again(self,publish):
  with self.captureOnCommitCallbacks(execute=True):
   p,created=create_reviewer(self.user,self.doc,'Reviewer','Topic',1)
   self.assertTrue(created);publish.assert_not_called()
  self.assertEqual(publish.call_count,1)
  _,created=create_reviewer(self.user,self.doc,'Another','Topic',1);self.assertFalse(created)
  self.assertEqual(publish.call_count,1)
 @patch('learning.reviewers.answer_question',return_value={'answer':json.dumps([ITEM])})
 def test_generation_drafts_explanations_provenance_and_duplicate_delivery(self,answer):
  p=self.paper(generation_status='queued')
  generate_reviewer.run(p.pk,str(p.generation_token));p.refresh_from_db()
  self.assertEqual(p.generation_status,'ready');q=p.questions.get();self.assertFalse(q.reviewed)
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
