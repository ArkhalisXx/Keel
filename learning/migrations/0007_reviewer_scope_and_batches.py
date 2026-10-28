from django.db import migrations, models


def populate_generated_count(apps, schema_editor):
    ExamPaper = apps.get_model('learning', 'ExamPaper')
    Question = apps.get_model('learning', 'Question')
    for paper in ExamPaper.objects.filter(kind='reviewer').iterator():
        paper.generated_count = Question.objects.filter(paper_id=paper.pk).count()
        paper.save(update_fields=['generated_count'])


class Migration(migrations.Migration):
    dependencies = [('learning', '0006_practicerun')]

    operations = [
        migrations.RemoveConstraint(model_name='exampaper', name='one_paper_per_document_kind'),
        migrations.AddConstraint(
            model_name='exampaper',
            constraint=models.UniqueConstraint(fields=('owner', 'document'), condition=models.Q(kind='exam'), name='one_exam_per_owner_document'),
        ),
        migrations.AddField(model_name='exampaper', name='generated_count', field=models.PositiveSmallIntegerField(default=0)),
        migrations.AddField(model_name='exampaper', name='generation_focus', field=models.CharField(max_length=240, blank=True)),
        migrations.AddField(model_name='exampaper', name='source_page_start', field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='exampaper', name='source_page_end', field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.RunPython(populate_generated_count, migrations.RunPython.noop),
    ]
