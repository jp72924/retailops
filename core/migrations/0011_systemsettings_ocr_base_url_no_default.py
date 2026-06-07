from django.db import migrations, models


# The hardcoded VEPay instance that older builds shipped as the default.
# RetailOps no longer points OCR at any third-party service automatically;
# operators must run their own VEPay instance and configure its URL.
BUNDLED_VEPAY_URL = 'https://vepay-api-5bja335wiq-pv.a.run.app'


def clear_bundled_vepay_url(apps, schema_editor):
    SystemSettings = apps.get_model('core', 'SystemSettings')
    SystemSettings.objects.filter(
        pk=1,
        ocr_base_url=BUNDLED_VEPAY_URL,
    ).update(ocr_base_url='')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0010_product_images'),
    ]

    operations = [
        migrations.AlterField(
            model_name='systemsettings',
            name='ocr_base_url',
            field=models.URLField(blank=True, default=''),
        ),
        migrations.RunPython(clear_bundled_vepay_url, migrations.RunPython.noop),
    ]
