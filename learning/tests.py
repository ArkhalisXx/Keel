import io
import json
import os
import tempfile
from unittest.mock import patch
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from .models import Document, Question, Attempt
from .services import retrieve, explain, extract_pages

class WorkflowTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.temp.name)
        self.override.enable()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.override.disable)
        self.user = User.objects.create_user('student', password='test-password-934')
        self.other = User.objects.create_user('other', password='test-password-934')
        self.client.force_login(self.user)
        self.q = Question.objects.create(topic='Hydrostatics', prompt='Unit of force?',choices=['N','kg','m','s'],correct_index=0,explanation='The newton is the SI unit of force.',reviewed=True)

    def test_registration_and_login(self):
        self.client.logout()
        response = self.client.post('/accounts/register/',{'username':'newstudent','password1':'strong-test-password-392','password2':'strong-test-password-392'})
        self.assertRedirects(response,'/')
        self.assertEqual(self.client.session['_auth_user_id'], str(User.objects.get(username='newstudent').pk))

    def test_upload_retrieval_download_and_delete(self):
        response = self.client.post('/library/',{'title':'Notes','file':SimpleUploadedFile('notes.txt',b'Buoyancy equals the weight of displaced water.')})
        self.assertRedirects(response,'/library/')
        doc = Document.objects.get()
        self.assertEqual(doc.owner,self.user)
        sources = retrieve([doc],'Explain buoyancy')
        self.assertIn('displaced water',sources[0]['text'])
        self.assertEqual(self.client.get(f'/documents/{doc.pk}/').status_code,200)
        response = self.client.post('/tutor/',{'documents':[doc.pk],'question':'Explain buoyancy'})
        self.assertContains(response,'Source passages')
        self.client.post(f'/documents/{doc.pk}/delete/')
        self.assertFalse(Document.objects.exists())

    def test_private_documents_and_attempts(self):
        doc = Document.objects.create(owner=self.other,title='Other account confidential notes',file='private.txt',pages=[{'page':1,'text':'secret buoyancy'}])
        self.assertNotContains(self.client.get('/library/'),'Other account confidential notes')
        self.assertEqual(self.client.get(f'/documents/{doc.pk}/').status_code,404)
        self.assertEqual(self.client.post(f'/documents/{doc.pk}/delete/').status_code,404)
        response = self.client.post('/tutor/',{'documents':[doc.pk],'question':'buoyancy'})
        self.assertContains(response,'Select a valid choice')
        a = Attempt.objects.create(owner=self.other,question=self.q,selected_index=0,correct=True)
        self.assertEqual(self.client.get(f'/results/{a.pk}/').status_code,404)

    def test_scoring_is_server_side_and_post_redirect_get(self):
        response = self.client.post(f'/practice/{self.q.pk}/',{'choice':'1','correct':'true'})
        a = Attempt.objects.get()
        self.assertFalse(a.correct)
        self.assertRedirects(response,f'/results/{a.pk}/')
        self.client.get(response.url)
        self.assertEqual(Attempt.objects.count(),1)
        self.assertContains(self.client.get('/'),'0%')

    def test_no_solution_before_answer_and_unreviewed_hidden(self):
        self.assertNotContains(self.client.get(f'/practice/{self.q.pk}/'),self.q.explanation)
        self.q.reviewed=False; self.q.save()
        self.assertEqual(self.client.get(f'/practice/{self.q.pk}/').status_code,404)
        self.assertNotContains(self.client.get('/practice/'),'Unit of force?')

    def test_invalid_choice_and_file_rejected(self):
        self.client.post(f'/practice/{self.q.pk}/',{'choice':'99'})
        self.assertEqual(Attempt.objects.count(),0)
        response=self.client.post('/library/',{'title':'Bad','file':SimpleUploadedFile('bad.exe',b'bad')})
        self.assertContains(response,'Supported formats')
        self.assertEqual(Document.objects.count(),0)

    def test_pages_and_authentication(self):
        for url in ['/','/library/','/practice/','/tutor/']:
            self.assertEqual(self.client.get(url).status_code,200)
        self.client.logout()
        for url in ['/','/library/','/practice/','/tutor/']:
            self.assertEqual(self.client.get(url).status_code,302)

    def test_docx_and_pdf_extraction(self):
        from docx import Document as Docx
        from pypdf import PdfWriter
        stream=io.BytesIO(); d=Docx();d.add_paragraph('Marine engineering notes');d.save(stream)
        self.assertIn('Marine',extract_pages(SimpleUploadedFile('notes.docx',stream.getvalue()))[0]['text'])
        stream=io.BytesIO(); w=PdfWriter();w.add_blank_page(width=100,height=100);w.write(stream)
        with self.assertRaises(ValueError): extract_pages(SimpleUploadedFile('scan.pdf',stream.getvalue()))

    @patch.dict(os.environ, {'AI_PROVIDER':'openai','OPENAI_API_KEY':''})
    def test_no_key_does_not_call_provider(self):
        with patch('urllib.request.urlopen') as call:
            self.assertIsNone(explain('question',[]));call.assert_not_called()

    def test_provider_response_formats(self):
        cases=[('openai','OPENAI_API_KEY',{'output':[{'content':[{'type':'output_text','text':'Explanation [1]'}]}]}),('gemini','GEMINI_API_KEY',{'candidates':[{'content':{'parts':[{'text':'Explanation [1]'}]}}]})]
        for provider,key,payload in cases:
            with self.subTest(provider=provider), patch.dict(os.environ,{'AI_PROVIDER':provider,key:'test-key'}), patch('urllib.request.urlopen') as call:
                call.return_value.__enter__.return_value=io.BytesIO(json.dumps(payload).encode())
                self.assertEqual(explain('force',[{'title':'Notes','page':1,'text':'Force is measured in newtons.'}]),'Explanation [1]')
                request=call.call_args.args[0]
                self.assertIn(b'Force is measured',request.data)

    def test_csrf_enforced(self):
        from django.test import Client
        client=Client(enforce_csrf_checks=True);client.force_login(self.user)
        self.assertEqual(client.post(f'/practice/{self.q.pk}/',{'choice':'0'}).status_code,403)
