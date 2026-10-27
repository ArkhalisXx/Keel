"""Server validation shared by the full assistant page and floating chat."""
from django import forms

from .models import Document


class AssistantForm(forms.Form):
    mode = forms.ChoiceField(choices=[
        ('study', 'Study coach'),
        ('documents', 'My documents'),
        ('web', 'Search the web'),
    ], initial='study')
    documents = forms.ModelMultipleChoiceField(
        queryset=Document.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text='Only documents that finished processing are available.')
    question = forms.CharField(max_length=2000, strip=True,
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': 'What would you like to learn?'}))

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['documents'].queryset = Document.objects.filter(
            owner=user, status=Document.Status.READY).defer('pages').order_by('title', 'pk')

    def clean(self):
        data = super().clean()
        if data.get('mode') == 'documents' and not data.get('documents'):
            self.add_error('documents', 'Select at least one ready document for document study.')
        if data.get('documents') is not None and len(data['documents']) > 8:
            self.add_error('documents', 'Select up to eight documents per conversation.')
        return data
