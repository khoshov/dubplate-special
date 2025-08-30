from celery import shared_task
import re
import os

from django.conf import settings
from records.models import Track
from yt_dlp import YoutubeDL


@shared_task
def dl_track(record_id, track_id, url: str, expected_artists: list[str]):
    # Получаем объект трека по ID
    try:
        track = Track.objects.get(id=track_id)
    except Track.DoesNotExist:
        print(f"Трек с ID {track_id} не найден")
        return

    if url:
        # 1. Проверяем метаданные без скачивания
        with YoutubeDL({'quiet': True, 'extract_flat': True}) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
                video_title = info.get('title', '').lower()
            except Exception as e:
                print(f"Ошибка получения информации: {str(e)}")
                return

        # 2. Проверяем совпадение с ожидаемыми данными
        count = 0
        for artist in expected_artists:
            if re.search(re.escape(artist.lower()), video_title):
                count += 1

        title_match = re.search(re.escape(track.title.lower()), video_title)

        if not (count and title_match):
            print("Видео не соответствует ожидаемому.")
            return

        # 3. Скачиваем если проверка пройдена
        print("Совпадение подтверждено. Начинаю загрузку...")

        # Формируем имя файла
        filename = f"{', '.join(expected_artists)} - {track.title}"
        # Формируем путь для сохранения (относительно MEDIA_ROOT)
        relative_path = os.path.join('tracks', str(record_id), f'{filename}.%(ext)s')
        full_path = os.path.join(settings.MEDIA_ROOT, relative_path)

        # Создаем директорию если не существует
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': full_path,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '320',
            }],
            'embed-metadata': True,
            'embed-thumbnail': False,
            'quiet': True
        }

        try:
            with YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            print("Загрузка успешно завершена!")

            track.audio_file = os.path.join('tracks', str(record_id), f'{filename}.mp3')
            track.save()

            return True
        except Exception as e:
            print(f"Ошибка при загрузке: {str(e)}")
            return False
