from unittest.mock import patch
from django.contrib.auth.models import User
from django.core import signing
from django.http import HttpResponse
from django.test import Client, TestCase, override_settings
from django.urls import path

from . import practice_groups as views
from .models import Attempt, Document, ExamPaper, PracticeRun, Question

urlpatterns = [path('practice/', views.grouped_library, name='practice'),
               path('practice/start/', views.start_practice, name='start_practice'),
               path('practice/sessions/<int:pk>/', views.practice_run, name='practice_run')]


@override_settings(ROOT_URLCONF=__name__)
class GroupedPracticeTests(TestCase):
    def setUp(self):
        self.user=User.objects.create_user('student')
        self.other=User.objects.create_user('other')
        self.client.force_login(self.user)
        self.doc=Document.objects.create(owner=self.user,title='My reference',file='paper.txt',status='ready')
        self.paper=ExamPaper.objects.create(owner=self.user,document=self.doc,title='My mock paper',topic='Naval architecture')

    def question(self, number, **kwargs):
        values=dict(owner=self.user,paper=self.paper,source_document=self.doc,source_number=number,
                    position=int(number.split('-')[-1]),topic='Naval architecture',prompt='Question '+number,
                    choices=['First','Second','Third','Fourth'],correct_index=0,
                    explanation='Reviewed explanation '+number,reference='Reference '+number,reviewed=True)
        values.update(kwargs)
        return Question.objects.create(**values)

    def groups(self, query=''):
        with patch('learning.practice_groups.render',return_value=HttpResponse()) as render:
            response=self.client.get('/practice/'+query)
            self.assertEqual(response.status_code,200)
            return render.call_args.args[2]

    def start(self, group=None):
        if group is None:group=list(self.groups()['groups'])[0]
        response=self.client.post('/practice/start/',{'group':group['key']})
        self.assertEqual(response.status_code,302)
        return PracticeRun.objects.latest('pk')

    def context(self, run):
        with patch('learning.practice_groups.render',return_value=HttpResponse()) as render:
            response=self.client.get(f'/practice/sessions/{run.pk}/')
            self.assertEqual(response.status_code,200)
            return render.call_args.args[2]

    def answer(self, run, card=0, answer='0', reveal=False):
        data={'action':'answer','card':str(card),'answer':answer}
        if reveal:data['reveal']='yes'
        return self.client.post(f'/practice/sessions/{run.pk}/',data)

    def test_existing_source_numbers_form_seven_sets_without_reimport(self):
        Question.objects.bulk_create([Question(owner=self.user,paper=self.paper,source_document=self.doc,
            source_number=f'{section}-{number}',position=index*100+number,topic='Naval architecture',
            prompt='Question',choices=['a','b','c','d'],correct_index=0,reviewed=True)
            for index,section in enumerate('ABCDEFG') for number in range(1,101)])
        context=self.groups()
        groups=list(context['groups'])
        self.assertEqual(context['question_count'],700)
        self.assertEqual([g['section'] for g in groups],list('ABCDEFG'))
        self.assertEqual([g['count'] for g in groups],[100]*7)
        run=self.start(groups[0])
        self.assertEqual(len(run.questions),100)
        self.assertTrue(all(q['number'].startswith('A-') for q in run.questions))
        self.assertTrue(all(q['source_document_id']==self.doc.pk for q in run.questions))

    def test_unsegmented_document_stays_one_collection_and_numeric_sets_sort_naturally(self):
        self.question('1');self.question('2')
        self.assertEqual(len(self.groups()['groups']),1)
        Question.objects.filter(paper=self.paper).delete()
        for value in ['10','2','1']:self.question(value+'-1')
        self.assertEqual([g['section'] for g in self.groups()['groups']],['1','2','10'])

    def test_group_card_progress_uses_only_owners_latest_answers(self):
        q=self.question('A-1');self.question('A-2')
        Attempt.objects.create(owner=self.other,question=q,selected_index=0,correct=True)
        Attempt.objects.create(owner=self.user,question=q,selected_index=1,correct=False)
        group=list(self.groups()['groups'])[0]
        self.assertEqual((group['attempted'],group['correct'],group['accuracy']),(1,0,0))
        Attempt.objects.create(owner=self.user,question=q,selected_index=0,correct=True)
        group=list(self.groups()['groups'])[0]
        self.assertEqual((group['attempted'],group['correct'],group['accuracy']),(1,1,100))
        self.assertEqual(list(self.groups('?progress=new')['groups'])[0]['count'],1)

    def test_group_keys_are_owned_signed_and_access_rechecked(self):
        self.question('A-1')
        group=list(self.groups()['groups'])[0]
        self.assertEqual(self.client.get('/practice/start/').status_code,405)
        self.assertEqual(self.client.post('/practice/start/',{'group':'tampered'}).status_code,400)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post('/practice/start/',{'group':group['key']}).status_code,400)
        self.client.force_login(self.user)
        Question.objects.filter(paper=self.paper).update(reviewed=False)
        self.client.post('/practice/start/',{'group':group['key']})
        self.assertEqual(PracticeRun.objects.count(),0)

    def test_no_answers_in_current_card_context_before_answer(self):
        self.question('A-1');self.question('A-2')
        run=self.start();context=self.context(run)
        self.assertFalse(context['feedback'])
        self.assertEqual(context['item']['index'],0)
        self.assertNotIn('correct_index',context['item'])
        self.assertNotIn('explanation',context['item'])
        self.assertNotIn('reference',context['item'])
        self.assertNotIn('items',context)
        self.assertIsNone(context['run']['score'])
        self.assertIsInstance(context['run'],dict)

    def test_answer_auto_advances_and_duplicate_post_never_skips_or_records_twice(self):
        self.question('A-1');self.question('A-2')
        run=self.start()
        self.answer(run,0,'0')
        context=self.context(run)
        self.assertEqual(context['item']['index'],1)
        self.assertFalse(context['feedback'])
        self.answer(run,0,'1')
        self.assertEqual(Attempt.objects.filter(owner=self.user).count(),1)
        self.assertEqual(self.context(run)['item']['index'],1)
        self.answer(run,1,'1');run.refresh_from_db()
        self.assertTrue(run.completed);self.assertEqual(run.score,1)
        self.assertEqual(len(self.context(run)['items']),2)
        self.answer(run,1,'0');self.assertEqual(Attempt.objects.filter(owner=self.user).count(),2)

    def test_optional_reveal_stays_on_answered_card_until_next(self):
        self.question('A-1');self.question('A-2')
        run=self.start();self.answer(run,0,'1',reveal=True)
        context=self.context(run)
        self.assertTrue(context['feedback']);self.assertEqual(context['item']['index'],0)
        self.assertEqual(context['item']['correct_index'],0)
        self.assertEqual(context['item']['selected_index'],1)
        self.assertIn('Reviewed explanation',context['item']['explanation'])
        self.answer(run,0,'0',reveal=True)
        self.assertEqual(Attempt.objects.count(),1)
        self.client.post(f'/practice/sessions/{run.pk}/',{'action':'next','card':'0'})
        context=self.context(run);self.assertEqual(context['item']['index'],1);self.assertFalse(context['feedback'])
        self.answer(run,1,'0',reveal=True);run.refresh_from_db();self.assertFalse(run.completed)
        self.client.post(f'/practice/sessions/{run.pk}/',{'action':'next','card':'1'})
        run.refresh_from_db();self.assertTrue(run.completed)

    def test_snapshot_scoring_survives_edits_and_deletions_and_unpublished_question_has_no_attempt(self):
        first=self.question('A-1');second=self.question('A-2')
        run=self.start()
        first.correct_index=1;first.explanation='Changed';first.save()
        self.answer(run,0,'0',reveal=True)
        context=self.context(run)
        self.assertTrue(context['item']['correct'])
        self.assertEqual(context['item']['explanation'],'Reviewed explanation A-1')
        self.client.post(f'/practice/sessions/{run.pk}/',{'action':'next','card':'0'})
        second.delete();self.answer(run,1,'0');run.refresh_from_db()
        self.assertTrue(run.completed);self.assertEqual(run.score,2)
        self.assertEqual(Attempt.objects.count(),1)

    def test_bad_future_card_and_invalid_answer_never_grade(self):
        self.question('A-1');self.question('A-2')
        run=self.start()
        with patch('learning.practice_groups.render',return_value=HttpResponse()) as render:
            self.answer(run,1,'0')
            self.assertTrue(render.call_args.args[2]['errors'])
            self.answer(run,0,'99')
            self.assertTrue(render.call_args.args[2]['errors'])
        self.assertEqual(Attempt.objects.count(),0)
        run.refresh_from_db();self.assertEqual(run.results,[])

    def test_run_is_private_and_csrf_enforced(self):
        self.question('A-1');run=self.start()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/practice/sessions/{run.pk}/').status_code,404)
        self.assertEqual(self.answer(run).status_code,404)
        csrf=Client(enforce_csrf_checks=True);csrf.force_login(self.user)
        self.assertEqual(csrf.post(f'/practice/sessions/{run.pk}/',{'action':'answer','card':'0','answer':'0'}).status_code,403)
