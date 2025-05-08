import discogs_client
from datetime import datetime
from django.conf import settings
from urllib.error import HTTPError

from apps.records.models import Artist, Label, Genre, Style, Record, Track, RecordFormats


def import_from_discogs(barcode):
    """
    Импортирует релиз из Discogs по штрих-коду
    :param barcode: штрих-код релиза
    :return: созданный объект Record или None
    """
    # Инициализация клиента Discogs
    d = discogs_client.Client('my_user_agent/1.0', user_token=settings.DISCOGS_TOKEN)

    try:
        # Поиск релиза
        results = d.search(barcode, type='barcode')
        if not results:
            print(f"Релиз с штрих-кодом {barcode} не найден")
            return None

        release = results[0]
        release.refresh()  # Загрузка полных данных

        # Получение URL обложки (первое изображение если есть)
        cover_url = release.images[0]['uri'] if release.images else None
        print(cover_url)

        # Обработка артистов
        artists = []
        for artist_data in release.artists:
            artist, _ = Artist.objects.get_or_create(
                discogs_id=artist_data.id,
                defaults={'name': artist_data.name}
            )
            artists.append(artist)

        # Обработка лейбла
        label = None
        if release.labels:
            label_data = release.labels[0]
            label, _ = Label.objects.get_or_create(
                discogs_id=label_data.id,
                defaults={
                    'name': label_data.name,
                    'description': f"Discogs ID: {label_data.id}"
                }
            )

        # Обработка жанров и стилей
        genres = [Genre.objects.get_or_create(name=name)[0] for name in release.genres]
        styles = [Style.objects.get_or_create(name=name)[0] for name in release.styles]

        # Определение формата
        format = determine_format(release.formats)

        # Создание записи о релизе
        record = Record.objects.create(
            title=release.title,
            discogs_id=release.id,
            cover_image_url=cover_url,
            label=label,
            release_date=datetime.strptime(release.released, '%Y-%m-%d').date() if hasattr(release,
                                                                                           'released') and release.released else None,
            catalog_number=release.labels[0].catno if release.labels else None,
            barcode=barcode,
            format=format,
            country=release.country,
            notes=release.notes,
            condition=getattr(release, 'condition', 'NM')[:3],  # Обрезаем до 3 символов
        )

        # Установка связей ManyToMany
        record.artists.set(artists)
        record.genres.set(genres)
        record.styles.set(styles)

        # Добавление треков
        if release.tracklist:
            for track_data in release.tracklist:
                Track.objects.create(
                    record=record,
                    position=track_data.position,
                    title=track_data.title,
                    duration=track_data.duration
                )

        print(f"Успешно импортирован: {record.title}")
        return record

    except HTTPError as e:
        if e.code == 403:
            print("Ошибка доступа 403: Проверьте токен Discogs")
        elif e.code == 404:
            print("Релиз не найден")
        else:
            print(f"HTTP ошибка: {e.code}")
        return None
    except Exception as e:
        print(f"Ошибка при импорте: {str(e)}")
        return None


def determine_format(formats_data):
    """
    Определяет формат релиза на основе данных Discogs
    """
    if not formats_data:
        return RecordFormats.OTHER

    format_name = formats_data[0]['name'].upper()
    qty = int(formats_data[0].get('qty', 1))

    format_mapping = {
        'LP': RecordFormats.LP,
        '2LP': RecordFormats.LP2,
        '3LP': RecordFormats.LP3,
        'EP': RecordFormats.EP,
        '7"': RecordFormats.SEVEN,
        '10"': RecordFormats.TEN,
        '12"': RecordFormats.TWELVE,
        'BOX': RecordFormats.BOX,
        'PICTURE DISC': RecordFormats.PIC,
        'SHAPED': RecordFormats.SHAPED,
        'FLEXI': RecordFormats.FLEXI,
        'ACETATE': RecordFormats.ACETATE,
        'TEST PRESSING': RecordFormats.TEST
    }

    # Обработка количества дисков
    if qty > 1 and format_name == 'LP':
        if qty == 2:
            return RecordFormats.LP2
        elif qty == 3:
            return RecordFormats.LP3

    return format_mapping.get(format_name, RecordFormats.OTHER)
