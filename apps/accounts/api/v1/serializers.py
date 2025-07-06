from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth import authenticate
from accounts.models import User, SMSVerification
from accounts.services import sms_service
from records.models import Order, OrderItem, Record
import re


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            'id',
            'username',
            'email',
            'first_name',
            'last_name',
            'phone',
            'birth_date',
            'address',
            'city',
            'country',
            'postal_code',
            'date_joined',
        ]
        read_only_fields = ['id', 'username', 'date_joined']


class UserUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            'first_name',
            'last_name',
            'phone',
            'birth_date',
            'address',
            'city',
            'country',
            'postal_code',
        ]

    def validate_phone(self, value):
        if value and len(value) < 10:
            raise serializers.ValidationError("Номер телефона должен содержать минимум 10 цифр")
        return value


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True)
    confirm_password = serializers.CharField(required=True)

    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError("Неверный текущий пароль")
        return value

    def validate(self, attrs):
        if attrs['new_password'] != attrs['confirm_password']:
            raise serializers.ValidationError("Новые пароли не совпадают")
        
        # Валидация нового пароля
        try:
            validate_password(attrs['new_password'], self.context['request'].user)
        except Exception as e:
            raise serializers.ValidationError(str(e))
        
        return attrs

    def save(self, **kwargs):
        user = self.context['request'].user
        user.set_password(self.validated_data['new_password'])
        user.save()
        return user


class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = [
            'username',
            'email',
            'password',
            'confirm_password',
            'first_name',
            'last_name',
        ]

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("Пользователь с таким email уже существует")
        return value

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("Пользователь с таким именем уже существует")
        return value

    def validate(self, attrs):
        if attrs['password'] != attrs['confirm_password']:
            raise serializers.ValidationError("Пароли не совпадают")
        
        # Валидация пароля
        try:
            validate_password(attrs['password'])
        except Exception as e:
            raise serializers.ValidationError(str(e))
        
        return attrs

    def create(self, validated_data):
        validated_data.pop('confirm_password')
        password = validated_data.pop('password')
        user = User.objects.create_user(**validated_data)
        user.set_password(password)
        user.save()
        return user


class UserLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()

    def validate(self, attrs):
        email = attrs.get('email')
        password = attrs.get('password')

        if email and password:
            try:
                user = User.objects.get(email=email)
                user = authenticate(username=user.username, password=password)
                if not user:
                    raise serializers.ValidationError("Неверный email или пароль")
                if not user.is_active:
                    raise serializers.ValidationError("Аккаунт деактивирован")
            except User.DoesNotExist:
                raise serializers.ValidationError("Неверный email или пароль")
        else:
            raise serializers.ValidationError("Необходимо указать email и пароль")

        attrs['user'] = user
        return attrs


class OrderItemHistorySerializer(serializers.ModelSerializer):
    record_title = serializers.CharField(source='record.title', read_only=True)
    record_artists = serializers.SerializerMethodField()
    record_cover_image = serializers.ImageField(source='record.cover_image', read_only=True)
    total_cost = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = [
            'id',
            'record_title',
            'record_artists',
            'record_cover_image',
            'price',
            'quantity',
            'total_cost',
        ]

    def get_record_artists(self, obj):
        return ", ".join([artist.name for artist in obj.record.artists.all()])

    def get_total_cost(self, obj):
        return obj.get_cost()


class OrderHistorySerializer(serializers.ModelSerializer):
    items = OrderItemHistorySerializer(many=True, read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    status_color = serializers.SerializerMethodField()
    items_count = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'id',
            'name',
            'phone',
            'address',
            'status',
            'status_display',
            'status_color',
            'total_price',
            'notes',
            'items_count',
            'items',
            'created',
            'modified',
        ]

    def get_status_color(self, obj):
        return obj.get_status_display_color()

    def get_items_count(self, obj):
        return obj.items.count()


class OrderStatusUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Order
        fields = ['status']

    def validate_status(self, value):
        valid_statuses = [choice[0] for choice in Order._meta.get_field('status').choices]
        if value not in valid_statuses:
            raise serializers.ValidationError("Недопустимый статус заказа")
        return value


class SendSMSSerializer(serializers.Serializer):
    phone_number = serializers.CharField(
        max_length=20,
        help_text="Российский номер телефона в любом формате"
    )
    
    def validate_phone_number(self, value):
        is_valid, result = sms_service.validate_russian_phone(value)
        if not is_valid:
            raise serializers.ValidationError(result)
        return result  # Возвращаем нормализованный номер
    
    def save(self):
        phone_number = self.validated_data['phone_number']
        
        # Создаем код верификации
        verification = SMSVerification.create_verification(phone_number)
        
        # Отправляем SMS
        success, message = sms_service.send_verification_code(
            phone_number, 
            verification.code
        )
        
        if not success:
            verification.delete()  # Удаляем код если не удалось отправить
            raise serializers.ValidationError(message)
        
        return {
            'message': message,
            'phone_number': phone_number,
            'expires_in': 300  # 5 минут в секундах
        }


class VerifySMSSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=20)
    code = serializers.CharField(max_length=6, min_length=6)
    
    def validate_phone_number(self, value):
        is_valid, result = sms_service.validate_russian_phone(value)
        if not is_valid:
            raise serializers.ValidationError(result)
        return result
    
    def validate_code(self, value):
        if not value.isdigit():
            raise serializers.ValidationError("Код должен содержать только цифры")
        return value
    
    def validate(self, attrs):
        phone_number = attrs['phone_number']
        code = attrs['code']
        
        # Ищем активную верификацию
        try:
            verification = SMSVerification.objects.filter(
                phone_number=phone_number,
                is_verified=False
            ).latest('created_at')
        except SMSVerification.DoesNotExist:
            raise serializers.ValidationError("Код не найден или уже использован")
        
        # Проверяем код
        is_valid, message = verification.verify_code(code)
        if not is_valid:
            raise serializers.ValidationError(message)
        
        attrs['verification'] = verification
        return attrs


class SMSLoginSerializer(VerifySMSSerializer):
    """
    Вход через SMS для существующих пользователей
    """
    
    def validate(self, attrs):
        attrs = super().validate(attrs)
        phone_number = attrs['phone_number']
        
        # Проверяем, есть ли пользователь с таким номером
        try:
            user = User.objects.get(phone=phone_number)
        except User.DoesNotExist:
            raise serializers.ValidationError(
                "Пользователь с таким номером не найден. Используйте регистрацию."
            )
        
        attrs['user'] = user
        return attrs


class SMSRegistrationSerializer(VerifySMSSerializer):
    """
    Регистрация через SMS для новых пользователей
    """
    email = serializers.EmailField(required=False, allow_blank=True)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    
    def validate_email(self, value):
        if value and User.objects.filter(email=value).exists():
            raise serializers.ValidationError("Пользователь с таким email уже существует")
        return value
    
    def validate(self, attrs):
        attrs = super().validate(attrs)
        phone_number = attrs['phone_number']
        
        # Проверяем, нет ли уже пользователя с таким номером
        if User.objects.filter(phone=phone_number).exists():
            raise serializers.ValidationError(
                "Пользователь с таким номером уже существует. Используйте вход."
            )
        
        return attrs
    
    def create(self, validated_data):
        phone_number = validated_data['phone_number']
        email = validated_data.get('email', '')
        first_name = validated_data.get('first_name', '')
        last_name = validated_data.get('last_name', '')
        
        # Генерируем username на основе номера телефона
        username = f"user_{phone_number}"
        
        # Создаем пользователя
        user = User.objects.create_user(
            username=username,
            email=email,
            phone=phone_number,
            first_name=first_name,
            last_name=last_name
        )
        
        return user


class ResendSMSSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=20)
    
    def validate_phone_number(self, value):
        is_valid, result = sms_service.validate_russian_phone(value)
        if not is_valid:
            raise serializers.ValidationError(result)
        return result
    
    def validate(self, attrs):
        phone_number = attrs['phone_number']
        
        # Проверяем, есть ли неподтвержденная верификация
        try:
            verification = SMSVerification.objects.filter(
                phone_number=phone_number,
                is_verified=False
            ).latest('created_at')
        except SMSVerification.DoesNotExist:
            raise serializers.ValidationError("Нет активной верификации для повторной отправки")
        
        # Проверяем, можно ли отправить повторно
        if not verification.can_resend():
            raise serializers.ValidationError("Повторную отправку можно сделать через 1 минуту")
        
        attrs['verification'] = verification
        return attrs
    
    def save(self):
        phone_number = self.validated_data['phone_number']
        old_verification = self.validated_data['verification']
        
        # Создаем новый код
        verification = SMSVerification.create_verification(phone_number)
        
        # Отправляем SMS
        success, message = sms_service.send_verification_code(
            phone_number, 
            verification.code
        )
        
        if not success:
            verification.delete()
            raise serializers.ValidationError(message)
        
        return {
            'message': message,
            'phone_number': phone_number,
            'expires_in': 300
        }