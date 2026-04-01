from django.db import migrations, models
 
 
class Migration(migrations.Migration):
 
    dependencies = [
        ('chatbot', '0001_initial'),
    ]
    
    operations = [
        #/!\ Nouvelle colonne pour stocker le résumé consolidé des messages archivés
        migrations.AddField(
            model_name='conversation',
            name='summary',
            field=models.TextField(
                blank=True,
                default='',
                help_text='Résumé consolidé des anciens messages archivés'
            ),
        ),
        migrations.AddField(
            model_name='conversation',
            name='archived_count',
            field=models.IntegerField(
                default=0,
                help_text='Nombre de messages supprimés lors des archivages'
            ),
        ),
        #/!\ Nouvelle colonne pour suivre la date du dernier archivage
        migrations.AddField(
            model_name='conversation',
            name='last_archived_at',
            field=models.DateTimeField(
                null=True,
                blank=True,
                help_text='Dernière date d archivage'
            ),
        ),
    ]
 