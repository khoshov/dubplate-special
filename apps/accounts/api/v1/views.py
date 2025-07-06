from rest_framework import status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet
from rest_framework.mixins import RetrieveModelMixin, UpdateModelMixin, ListModelMixin
from rest_framework.views import APIView
from rest_framework.authtoken.models import Token
from django.contrib.auth import login, logout
from django.shortcuts import get_object_or_404

from accounts.models import User
from records.models import Order
from .serializers import (
    UserProfileSerializer,
    UserUpdateSerializer,
    ChangePasswordSerializer,
    UserRegistrationSerializer,
    UserLoginSerializer,
    OrderHistorySerializer,
    OrderStatusUpdateSerializer,
    SendSMSSerializer,
    VerifySMSSerializer,
    SMSLoginSerializer,
    SMSRegistrationSerializer,
    ResendSMSSerializer,
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
        if self.action == 'update' or self.action == 'partial_update':
            return UserUpdateSerializer
        return UserProfileSerializer

    @action(detail=False, methods=['post'], serializer_class=ChangePasswordSerializer)
    def change_password(self, request):
        """
        Изменение пароля пользователя
        """
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(
                {'message': 'Пароль успешно изменен'}, 
                status=status.HTTP_200_OK
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['delete'])
    def delete_account(self, request):
        """
        Деактивация аккаунта пользователя
        """
        user = request.user
        user.is_active = False
        user.save()
        logout(request)
        return Response(
            {'message': 'Аккаунт успешно деактивирован'}, 
            status=status.HTTP_200_OK
        )


class AuthViewSet(GenericViewSet):
    """
    ViewSet для аутентификации пользователей
    """
    permission_classes = [permissions.AllowAny]

    @action(detail=False, methods=['post'], serializer_class=UserRegistrationSerializer)
    def register(self, request):
        """
        Регистрация нового пользователя
        """
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            token, created = Token.objects.get_or_create(user=user)
            return Response({
                'user': UserProfileSerializer(user).data,
                'token': token.key,
                'message': 'Пользователь успешно зарегистрирован'
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], serializer_class=UserLoginSerializer)
    def login(self, request):
        """
        Вход пользователя в систему
        """
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            user = serializer.validated_data['user']
            login(request, user)
            token, created = Token.objects.get_or_create(user=user)
            return Response({
                'user': UserProfileSerializer(user).data,
                'token': token.key,
                'message': 'Успешный вход в систему'
            }, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'])
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
                {'message': 'Успешный выход из системы'}, 
                status=status.HTTP_200_OK
            )
        return Response(
            {'message': 'Пользователь не был аутентифицирован'}, 
            status=status.HTTP_400_BAD_REQUEST
        )


class UserDetailView(APIView):
    """
    Получение информации о текущем пользователе
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = UserProfileSerializer(request.user)
        return Response(serializer.data)


class OrderHistoryViewSet(ListModelMixin, RetrieveModelMixin, GenericViewSet):
    """
    ViewSet для работы с историей заказов пользователя
    """
    serializer_class = OrderHistorySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """
        Возвращает только заказы текущего пользователя
        """
        return Order.objects.filter(user=self.request.user).prefetch_related(
            'items__record__artists',
            'items__record'
        ).order_by('-created')

    def list(self, request, *args, **kwargs):
        """
        Получить список всех заказов пользователя
        """
        queryset = self.get_queryset()
        
        # Фильтрация по статусу
        status_filter = request.query_params.get('status', None)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Фильтрация по дате
        date_from = request.query_params.get('date_from', None)
        date_to = request.query_params.get('date_to', None)
        if date_from:
            queryset = queryset.filter(created__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(created__date__lte=date_to)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        """
        Получить детальную информацию о конкретном заказе
        """
        order = get_object_or_404(
            self.get_queryset(),
            pk=kwargs.get('pk')
        )
        serializer = self.get_serializer(order)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def statistics(self, request):
        """
        Получить статистику заказов пользователя
        """
        orders = self.get_queryset()
        
        # Подсчет заказов по статусам
        status_counts = {}
        for status_choice in Order._meta.get_field('status').choices:
            status_code = status_choice[0]
            status_counts[status_code] = orders.filter(status=status_code).count()
        
        # Общая статистика
        total_orders = orders.count()
        total_spent = sum(order.total_price for order in orders if order.total_price)
        
        return Response({
            'total_orders': total_orders,
            'total_spent': total_spent,
            'status_counts': status_counts,
            'average_order_value': total_spent / total_orders if total_orders > 0 else 0
        })

    @action(detail=False, methods=['get'])
    def status_choices(self, request):
        """
        Получить список доступных статусов заказов
        """
        choices = []
        for status_choice in Order._meta.get_field('status').choices:
            choices.append({
                'value': status_choice[0],
                'label': status_choice[1]
            })
        return Response(choices)


class SMSAuthViewSet(GenericViewSet):
    """
    ViewSet для SMS аутентификации
    """
    permission_classes = [permissions.AllowAny]

    @action(detail=False, methods=['post'], serializer_class=SendSMSSerializer)
    def send_code(self, request):
        """
        Отправка SMS кода для верификации номера телефона
        """
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            try:
                result = serializer.save()
                return Response(result, status=status.HTTP_200_OK)
            except Exception as e:
                return Response(
                    {'error': str(e)}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], serializer_class=VerifySMSSerializer)
    def verify_code(self, request):
        """
        Проверка SMS кода (без аутентификации)
        """
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            return Response(
                {'message': 'Код подтвержден успешно'}, 
                status=status.HTTP_200_OK
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], serializer_class=SMSLoginSerializer)
    def login(self, request):
        """
        Вход через SMS для существующих пользователей
        """
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            user = serializer.validated_data['user']
            login(request, user)
            token, created = Token.objects.get_or_create(user=user)
            
            return Response({
                'user': UserProfileSerializer(user).data,
                'token': token.key,
                'message': 'Успешный вход через SMS'
            }, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], serializer_class=SMSRegistrationSerializer)
    def register(self, request):
        """
        Регистрация через SMS для новых пользователей
        """
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            token, created = Token.objects.get_or_create(user=user)
            
            return Response({
                'user': UserProfileSerializer(user).data,
                'token': token.key,
                'message': 'Пользователь успешно зарегистрирован через SMS'
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], serializer_class=ResendSMSSerializer)
    def resend_code(self, request):
        """
        Повторная отправка SMS кода
        """
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            try:
                result = serializer.save()
                return Response(result, status=status.HTTP_200_OK)
            except Exception as e:
                return Response(
                    {'error': str(e)}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'])
    def check_phone(self, request):
        """
        Проверка, зарегистрирован ли номер телефона
        """
        phone_number = request.query_params.get('phone_number')
        if not phone_number:
            return Response(
                {'error': 'Номер телефона обязателен'}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        # Валидируем номер
        from accounts.services import sms_service
        is_valid, result = sms_service.validate_russian_phone(phone_number)
        if not is_valid:
            return Response(
                {'error': result}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        normalized_phone = result
        user_exists = User.objects.filter(phone=normalized_phone).exists()

        return Response({
            'phone_number': normalized_phone,
            'user_exists': user_exists,
            'action': 'login' if user_exists else 'register'
        })