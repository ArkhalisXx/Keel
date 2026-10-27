from pathlib import Path
from django import forms
from django.conf import settings
from .models import Document

class UploadForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = ['title', 'file']
    def clean_file(self):
        file = self.cleaned_data['file']
        if file.size > settings.DOCUMENT_MAX_UPLOAD_MB * 1024 * 1024:
            raise forms.ValidationError(f'Choose a document no larger than {settings.DOCUMENT_MAX_UPLOAD_MB} MB.')
        if Path(file.name).suffix.lower() not in {'.pdf', '.docx', '.txt'}:
            raise forms.ValidationError('Supported formats: PDF, DOCX, TXT.')
        return file

class TutorForm(forms.Form):
    documents = forms.ModelMultipleChoiceField(queryset=Document.objects.none(), required=True, widget=forms.CheckboxSelectMultiple)
    question = forms.CharField(max_length=2000, widget=forms.Textarea(attrs={'rows': 4, 'placeholder': 'Ask about a concept in your documents…'}))
    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['documents'].queryset = Document.objects.filter(owner=user, status=Document.Status.READY).defer('pages')
