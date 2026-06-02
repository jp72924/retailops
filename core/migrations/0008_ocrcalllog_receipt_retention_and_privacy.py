from django.db import migrations, models
import django.db.models.deletion
import core.models


CLOUD_RUN_VEPAY_URL = 'https://vepay-api-5bja335wiq-pv.a.run.app'
OLD_DEFAULT_VEPAY_URL = 'https://vepay-api.fly.dev'


def update_default_vepay_url(apps, schema_editor):
    SystemSettings = apps.get_model('core', 'SystemSettings')
    SystemSettings.objects.filter(
        pk=1,
        ocr_base_url=OLD_DEFAULT_VEPAY_URL,
    ).update(ocr_base_url=CLOUD_RUN_VEPAY_URL)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_payment_ocr_receipt_data_payment_origin_bank_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='systemsettings',
            name='delete_receipt_image_after_days',
            field=models.PositiveIntegerField(default=90),
        ),
        migrations.AlterField(
            model_name='payment',
            name='receipt_image',
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to=core.models.receipt_image_upload_path,
            ),
        ),
        migrations.AlterField(
            model_name='systemsettings',
            name='ocr_base_url',
            field=models.URLField(default=CLOUD_RUN_VEPAY_URL),
        ),
        migrations.CreateModel(
            name='OcrCallLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('request_id', models.CharField(blank=True, max_length=128)),
                ('status', models.CharField(db_index=True, max_length=50)),
                ('latency_ms', models.PositiveIntegerField(blank=True, null=True)),
                ('bytes_sent', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('kiosk_station', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='ocr_call_logs', to='core.kioskstation')),
                ('sales_order', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='ocr_call_logs', to='core.salesorder')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='ocrcalllog',
            index=models.Index(fields=['created_at'], name='ocr_created_idx'),
        ),
        migrations.AddIndex(
            model_name='ocrcalllog',
            index=models.Index(fields=['status', 'created_at'], name='ocr_status_created_idx'),
        ),
        migrations.RunPython(update_default_vepay_url, migrations.RunPython.noop),
    ]
