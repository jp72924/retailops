from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_ocrcalllog_receipt_retention_and_privacy'),
    ]

    operations = [
        migrations.AddField(
            model_name='systemsettings',
            name='receipt_image_required_for_receipt_methods',
            field=models.BooleanField(default=True),
        ),
    ]
