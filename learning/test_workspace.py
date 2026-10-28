from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from .models import Attempt, Document, ExamPaper, Question


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class WorkspaceTests(TestCase):
    def setUp(self):
        self.user=User.objects.create_user('reader')
        self.other=User.objects.create_user('other')
        self.client.force_login(self.user)
        self.shared=Question.objects.create(topic='Stability',prompt='Shared question',choices=['a','b','c','d'],correct_index=0,explanation='Reviewed explanation',reviewed=True)
        self.private=Question.objects.create(owner=self.other,topic='Secret',prompt='Private question',choices=['a','b','c','d'],correct_index=0,explanation='Private explanation',reviewed=True)

    def test_dashboard_counts_only_user_data_and_visible_questions(self):
        Attempt.objects.create(owner=self.other,question=self.shared,selected_index=0,correct=True)
        Attempt.objects.create(owner=self.user,question=self.shared,selected_index=1,correct=False)
        Document.objects.create(owner=self.other,title='Hidden reference',file='hidden.txt')
        response=self.client.get('/')
        self.assertEqual(response.context['question_count'],1)
        self.assertEqual(response.context['documents'],0)
        self.assertEqual(response.context['total'],1)
        self.assertEqual(response.context['accuracy'],0)
        self.assertEqual(sum(x['count'] for x in response.context['chart_days']),1)
        self.assertContains(response,'assistant-launcher')
        self.assertNotContains(response,'Private question')

    def test_private_questions_cannot_be_viewed_or_answered(self):
        self.assertNotContains(self.client.get('/practice/'),'Private question')
        self.assertEqual(self.client.get(f'/practice/{self.private.pk}/').status_code,404)
        self.assertEqual(self.client.post(f'/practice/{self.private.pk}/',{'choice':'0'}).status_code,404)
        self.assertFalse(Attempt.objects.exists())

    def test_practice_progress_filters_use_own_latest_attempt(self):
        Attempt.objects.create(owner=self.other,question=self.shared,selected_index=0,correct=True)
        self.assertContains(self.client.get('/practice/?view=questions&progress=new'),'Shared question')
        Attempt.objects.create(owner=self.user,question=self.shared,selected_index=1,correct=False)
        self.assertContains(self.client.get('/practice/?view=questions&progress=review'),'Shared question')
        Attempt.objects.create(owner=self.user,question=self.shared,selected_index=0,correct=True)
        self.assertNotContains(self.client.get('/practice/?view=questions&progress=review'),'Shared question')
        self.assertContains(self.client.get('/practice/?view=questions&progress=correct'),'Shared question')

    def test_library_search_sort_status_and_file_type(self):
        for title,file,status in [('Zulu','a.pdf','ready'),('Alpha','b.txt','ready'),('Pending','c.pdf','queued')]:
            Document.objects.create(owner=self.user,title=title,file=file,status=status)
        Document.objects.create(owner=self.other,title='Private file',file='hidden.pdf',status='ready')
        response=self.client.get('/library/?type=pdf&status=ready&sort=title')
        self.assertEqual([doc.title for doc in response.context['documents']],['Zulu'])
        response=self.client.get('/library/?q=Al&sort=title')
        self.assertEqual([doc.title for doc in response.context['documents']],['Alpha'])
        response=self.client.get('/library/?sort=malicious')
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,'Private file')

    def test_pagination_keeps_filter_values_and_stays_private(self):
        for i in range(15):
            Document.objects.create(owner=self.user,title=f'Notes {i}',file='x.txt')
        response=self.client.get('/library/?q=Notes&type=txt&sort=title')
        self.assertContains(response,'page=2')
        self.assertContains(response,'type=txt')
        self.assertEqual(len(response.context['documents']),12)

    def test_assistant_history_json_is_escaped_not_executable(self):
        session=self.client.session
        session['assistant_scope']=[self.user.pk,'study',[]]
        session['assistant_history']=[{'role':'assistant','content':'</script><script>alert(1)</script>'}]
        session.save()
        response=self.client.get('/assistant/')
        self.assertNotContains(response,'</script><script>alert(1)</script>')
        self.assertContains(response,'\\u003C/script\\u003E')
