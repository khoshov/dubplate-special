import vk_api
import requests
from datetime import datetime
import time


class VKAutoPoster:
    def __init__(self, token, group_id):
        self.token = token
        self.group_id = group_id
        self.vk = vk_api.VkApi(token=token)

    def post_text(self, message):
        """Публикация текстового поста"""
        try:
            response = self.vk.method('wall.post', {
                'owner_id': self.group_id,
                'message': message,
                'from_group': 1
            })
            print(f"Пост опубликован! ID: {response['post_id']}")
            return response
        except Exception as e:
            print(f"Ошибка: {e}")

    def post_with_photo(self, message, photo_url):
        """Публикация поста с фотографией"""
        try:
            # Загружаем фото на сервер ВК
            upload_url = self.vk.method('photos.getWallUploadServer', {
                'group_id': abs(self.group_id)
            })['upload_url']

            # Загружаем файл
            photo_data = requests.get(photo_url).content
            files = {'photo': ('photo.jpg', photo_data, 'image/jpeg')}
            upload_response = requests.post(upload_url, files=files).json()

            # Сохраняем фото
            save_response = self.vk.method('photos.saveWallPhoto', {
                'group_id': abs(self.group_id),
                'photo': upload_response['photo'],
                'server': upload_response['server'],
                'hash': upload_response['hash']
            })[0]

            # Публикуем пост
            attachment = f"photo{save_response['owner_id']}_{save_response['id']}"
            response = self.vk.method('wall.post', {
                'owner_id': self.group_id,
                'message': message,
                'attachment': attachment,
                'from_group': 1
            })
            print(f"Пост с фото опубликован! ID: {response['post_id']}")
            return response

        except Exception as e:
            print(f"Ошибка: {e}")

    def schedule_post(self, message, publish_date):
        """Отложенная публикация"""
        try:
            # Convert datetime to timestamp
            timestamp = int(publish_date.timestamp())

            response = self.vk.method('wall.post', {
                'owner_id': self.group_id,
                'message': message,
                'publish_date': timestamp,
                'from_group': 1
            })
            print(f"Отложенный пост создан! ID: {response['post_id']}")
            return response
        except Exception as e:
            print(f"Ошибка: {e}")


# Использование
if __name__ == "__main__":
    # Настройки
    ACCESS_TOKEN = "vk1.a.VftkauZ2qlj5LJVuud4dc7IBlM-O4ImQqz9vrwWgfLzjhBLcirayJ2jygwHrZHgKDLn4pP8LZr3ANwCUuhSiTrfPOjI3BQtSylPjRnaE873pEHzNtVIvpT8OnqCZFXUyh_KtWx5nUrRJekYKNKrJeEua6N9FR-k_pIb7p3g_ZugBQUOFCYQaRNuTJJGlqQnHrU3ToF7IdMfYvghkuYqXcQ"
    GROUP_ID = -225812294  # ID группы с минусом

    poster = VKAutoPoster(ACCESS_TOKEN, GROUP_ID)

    # Простой текст
    poster.post_text("Привет, этот пост создан скриптом!")

    # Пост с фото
    # poster.post_with_photo("Пост с картинкой!", "https://example.com/photo.jpg")

    # Отложенный пост
    # from datetime import datetime, timedelta
    # future_time = datetime.now() + timedelta(hours=1)
    # poster.schedule_post("Будущий пост", future_time)