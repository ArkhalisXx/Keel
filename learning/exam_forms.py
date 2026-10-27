from django import forms
from .models import Document, Question


class PaperImportForm(forms.Form):
    title = forms.CharField(max_length=200, help_text='A name for this practice paper.')
    topic = forms.CharField(max_length=120, help_text='For example: Hydrostatics or Ship Construction.')
    document = forms.ModelChoiceField(queryset=Document.objects.none(), help_text='Upload the paper in Library first and wait until it is Ready.')

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['document'].queryset = Document.objects.filter(owner=user, status=Document.Status.READY).defer('pages').order_by('title', 'pk')


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
        self.fields['explanation'].required = publish
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
