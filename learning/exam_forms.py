from django import forms
from .models import Document, Question


class PaperImportForm(forms.Form):
    kind = forms.ChoiceField(label='Create from this document', initial='auto', choices=[('auto', 'Automatic: import a paper or generate a reviewer'), ('exam', 'Import an exam paper and its answer key'), ('reviewer', 'Generate a Q&A reviewer from notes / a textbook')], required=False)
    source_mode = forms.ChoiceField(label='Question source', initial='notes', required=False,
        choices=[('notes', 'Create new questions from notes'), ('activities', 'Use textbook activities, quizzes & exercises')],
        help_text='Reuse questions already printed in the document, or create new ones from its explanations.')
    question_count = forms.IntegerField(label='AI reviewer questions', initial=20, min_value=1, max_value=100, required=False, help_text='1–100 questions, generated in small background batches. Larger sets use more API quota.')

    title = forms.CharField(max_length=200, help_text='A name for this practice paper.')
    topic = forms.CharField(max_length=120, help_text='For example: Hydrostatics or Ship Construction.')
    document = forms.ModelChoiceField(queryset=Document.objects.none(), help_text='Upload the paper in Library first and wait until it is Ready.')
    focus = forms.CharField(label='Topic or chapter keywords', max_length=240, required=False,
                           help_text='Optional: use words found in the document, such as transverse stability.',
                           widget=forms.TextInput(attrs={'placeholder': 'e.g. Buoyancy, transverse stability'}))
    page_start = forms.IntegerField(label='From page', min_value=1, required=False,
                                   help_text='Document page number, starting at 1; printed page labels can differ.')
    page_end = forms.IntegerField(label='To page', min_value=1, required=False)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['document'].queryset = Document.objects.filter(owner=user, status=Document.Status.READY).defer('pages').order_by('title', 'pk')

    def clean(self):
        cleaned = super().clean()
        cleaned['source_mode'] = cleaned.get('source_mode') or 'notes'
        # Exam imports retain the paper's full numbering and answer-key sections.
        if cleaned.get('kind') == 'exam':
            cleaned.update(focus='', page_start=None, page_end=None, source_mode='notes')
            return cleaned
        document = cleaned.get('document')
        if document and not any(field in self.errors for field in ('focus', 'page_start', 'page_end')):
            from .assistant import AIError
            from .reviewer_sources import select_sources, validate_scope
            try:
                focus, start, end = validate_scope(document, focus=cleaned.get('focus', ''),
                                                  page_start=cleaned.get('page_start'), page_end=cleaned.get('page_end'))
                if focus or cleaned['source_mode'] == 'activities':
                    select_sources(document, focus=focus, page_start=start, page_end=end, source_mode=cleaned['source_mode'])
            except AIError as error:
                self.add_error(None, str(error))
            else:
                cleaned.update(focus=focus, page_start=start, page_end=end)
        return cleaned


class QuestionReviewForm(forms.ModelForm):
    choice_a = forms.CharField(label='Choice A', widget=forms.Textarea(attrs={'rows': 2}))
    choice_b = forms.CharField(label='Choice B', widget=forms.Textarea(attrs={'rows': 2}))
    choice_c = forms.CharField(label='Choice C', widget=forms.Textarea(attrs={'rows': 2}))
    choice_d = forms.CharField(label='Choice D', widget=forms.Textarea(attrs={'rows': 2}))

    class Meta:
        model = Question
        fields = ['topic', 'prompt', 'correct_index', 'explanation', 'reference']
        widgets = {'prompt': forms.Textarea(attrs={'rows': 4}), 'explanation': forms.Textarea(attrs={'rows': 5})}

    def __init__(self, *args, publish=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_fields(['topic', 'prompt', 'choice_a', 'choice_b', 'choice_c', 'choice_d', 'correct_index', 'explanation', 'reference'])
        self.publish = publish
        self.fields['explanation'].required = False
        self.fields['explanation'].help_text = 'Optional. You can publish a question using its answer key without writing an explanation.'
        self.fields['correct_index'].required = publish
        self.fields['correct_index'].label = 'Correct answer (1=A, 2=B, 3=C, 4=D)'
        if self.instance.pk:
            for label, value in zip('abcd', self.instance.choices):
                self.fields[f'choice_{label}'].initial = value

    def clean(self):
        cleaned = super().clean()
        self.instance.choices = [cleaned.get(f'choice_{letter}', '') for letter in 'abcd']
        self.instance.reviewed = self.publish
        return cleaned
