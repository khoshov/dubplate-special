from django.db import migrations

def seed_presets(apps, schema_editor):
    Genre = apps.get_model('records', 'Genre')
    Style = apps.get_model('records', 'Style')
    Format = apps.get_model('records', 'Format')

    # те же списки, что в TextChoices (жёстко, чтобы миграция была стабильной)
    genres = [
        "not specified","House","Techno","Electro","Breaks","Drum & Bass","Garage",
        "Ambient","Experimental","Disco","Hip-Hop","Reggae","Dub","Trance"
    ]
    styles = [
        "not specified","Deep House","Minimal","Acid","Detroit","Progressive","Jungle",
        "Breakbeat","UK Garage","IDM","Downtempo","Lo-Fi","Hardcore","Italo","Leftfield"
    ]
    formats = [
        'not specified','7"','10"','12"','EP','Single','LP','2LP','3LP','4LP','Box Set','Picture Disc'
    ]

    for name in genres:
        Genre.objects.get_or_create(name=name)
    for name in styles:
        Style.objects.get_or_create(name=name)
    for name in formats:
        Format.objects.get_or_create(name=name)

def unseed_presets(apps, schema_editor):
    # не удаляем данные при откате, оставим пустым
    pass

class Migration(migrations.Migration):

    dependencies = [
        ('records', '0005_alter_format_name_alter_genre_name_alter_style_name'),
    ]

    operations = [
        migrations.RunPython(seed_presets, unseed_presets),
    ]
