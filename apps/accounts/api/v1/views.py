from rest_framework import permissions, status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.mixins import RetrieveModelMixin, UpdateModelMixin
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet

from django.contrib.auth import login, logout

from accounts.models import User

from .serializers import (
    ChangePasswordSerializer,
    SendSMSSerializer,
    UniversalLoginSerializer,
    UniversalSMSAuthSerializer,
    UserLoginSerializer,
    UserProfileSerializer,
    UserRegistrationSerializer,
    UserUpdateSerializer,
)


class UserProfileViewSet(RetrieveModelMixin, UpdateModelMixin, GenericViewSet):
    """
    ViewSet для работы с профилем пользователя
    """

    serializer_class = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user

    def get_serializer_class(self):
        if self.action == "update" or self.action == "partial_update":
            return UserUpdateSerializer
        return UserProfileSerializer

    @action(detail=False, methods=["post"], serializer_class=ChangePasswordSerializer)
    def change_password(self, request):
        """
        Изменение пароля пользователя
        """
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(
                {"message": "Пароль успешно изменен"}, status=status.HTTP_200_OK
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["delete"])
    def delete_account(self, request):
        """
        Деактивация аккаунта пользователя
        """
        user = request.user
        user.is_active = False
        user.save()
        logout(request)
        return Response(
            {"message": "Аккаунт успешно деактивирован"}, status=status.HTTP_200_OK
        )


class AuthViewSet(GenericViewSet):
    """
    ViewSet для аутентификации пользователей
    """

    permission_classes = [permissions.AllowAny]

    @action(detail=False, methods=["post"], serializer_class=UserRegistrationSerializer)
    def register(self, request):
        """
        Регистрация нового пользователя
        """
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            token, created = Token.objects.get_or_create(user=user)
            return Response(
                {
                    "user": UserProfileSerializer(user).data,
                    "token": token.key,
                    "message": "Пользователь успешно зарегистрирован",
                },
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["post"], serializer_class=UserLoginSerializer)
    def login(self, request):
        """
        Вход пользователя в систему
        """
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            user = serializer.validated_data["user"]
            login(request, user)
            token, created = Token.objects.get_or_create(user=user)
            return Response(
                {
                    "user": UserProfileSerializer(user).data,
                    "token": token.key,
                    "message": "Успешный вход в систему",
                },
                status=status.HTTP_200_OK,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["post"], serializer_class=UniversalLoginSerializer)
    def universal_login(self, request):
        """
        Универсальный вход через email/пароль или телефон/SMS код
        """
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            user = serializer.validated_data["user"]
            login_type = serializer.validated_data["login_type"]
            login(request, user)
            token, created = Token.objects.get_or_create(user=user)
            return Response(
                {
                    "user": UserProfileSerializer(user).data,
                    "token": token.key,
                    "login_type": login_type,
                    "message": f"Успешный вход через {login_type}",
                },
                status=status.HTTP_200_OK,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["post"])
    def logout(self, request):
        """
        Выход пользователя из системы
        """
        if request.user.is_authenticated:
            # Удаляем токен пользователя
            try:
                token = Token.objects.get(user=request.user)
                token.delete()
            except Token.DoesNotExist:
                pass

            logout(request)
            return Response(
                {"message": "Успешный выход из системы"}, status=status.HTTP_200_OK
            )
        return Response(
            {"message": "Пользователь не был аутентифицирован"},
            status=status.HTTP_400_BAD_REQUEST,
        )


class UserDetailView(APIView):
    """
    Получение информации о текущем пользователе
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = UserProfileSerializer(request.user)
        return Response(serializer.data)


class SMSAuthViewSet(GenericViewSet):
    """
    Упрощенный ViewSet для SMS аутентификации

    Эндпоинты:
    - send_code: отправка SMS кода (с автоматической переотправкой)
    - auth: универсальная авторизация (автоматический вход/регистрация)
    - check_phone: проверка существования номера телефона
    """

    permission_classes = [permissions.AllowAny]

    @action(detail=False, methods=["post"], serializer_class=SendSMSSerializer)
    def send_code(self, request):
        """
        Отправка SMS кода для верификации номера телефона
        Автоматически поддерживает переотправку (если прошло больше 1 минуты)
        """
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            try:
                result = serializer.save()
                return Response(result, status=status.HTTP_200_OK)
            except Exception as e:
                return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["post"], serializer_class=UniversalSMSAuthSerializer)
    def auth(self, request):
        """
        Универсальная SMS авторизация
        Автоматически определяет нужно ли регистрировать пользователя или войти
        """
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            result = serializer.save()
            user = result["user"]
            action = result["action"]

            # Авторизуем пользователя
            login(request, user)
            token, created = Token.objects.get_or_create(user=user)

            return Response(
                {
                    "user": UserProfileSerializer(user).data,
                    "token": token.key,
                    "action": action,
                    "message": result["message"],
                },
                status=status.HTTP_200_OK
                if action == "login"
                else status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["get"])
    def check_phone(self, request):
        """
        Проверка, зарегистрирован ли номер телефона
        """
        phone_number = request.query_params.get("phone_number")
        if not phone_number:
            return Response(
                {"error": "Номер телефона обязателен"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Валидируем номер
        from accounts.services import sms_service

        is_valid, result = sms_service.validate_russian_phone(phone_number)
        if not is_valid:
            return Response({"error": result}, status=status.HTTP_400_BAD_REQUEST)

        normalized_phone = result
        user_exists = User.objects.filter(phone=normalized_phone).exists()

        return Response(
            {
                "phone_number": normalized_phone,
                "user_exists": user_exists,
                "action": "login" if user_exists else "register",
            }
        )
