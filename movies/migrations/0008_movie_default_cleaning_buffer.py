from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('movies', '0007_data_migration'),
    ]

    operations = [
        migrations.AddField(
            model_name='movie',
            name='default_cleaning_buffer_minutes',
            field=models.PositiveIntegerField(
                default=30,
                help_text='Default buffer/cleaning time between shows (minutes) for new showtimes',
            ),
        ),
        migrations.AlterField(
            model_name='movie',
            name='end_date',
            field=models.DateField(
                blank=True,
                help_text='Available until (last bookable day, inclusive)',
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name='movie',
            name='release_date',
            field=models.DateField(
                blank=True,
                help_text='Available from (first bookable day, inclusive)',
                null=True,
            ),
        ),
    ]
