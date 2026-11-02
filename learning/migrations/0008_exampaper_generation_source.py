from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('learning', '0007_reviewer_scope_and_batches')]
    operations = [migrations.AddField(
        model_name='exampaper', name='generation_source',
        field=models.CharField(max_length=12, default='notes', choices=[
            ('notes', 'Generate from notes'), ('activities', 'Use textbook activities and quizzes')]),
    ), migrations.AddField(
        model_name='question', name='activity_source_id',
        field=models.CharField(max_length=64, blank=True, default=''),
    )]
