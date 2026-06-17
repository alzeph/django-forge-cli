from rest_framework.utils.serializer_helpers import ReturnDict, ReturnList
from forge_test.public.helpers import ForgeCase, ConfigForgeCase
from forge_auth.models import Group
from django.contrib.auth import get_user_model
from django.urls import reverse

User = get_user_model()


class GroupTestCase(ForgeCase):
    config: ConfigForgeCase = {
        'factory_params': {
            'max_depth': 7,
            'create_m2m': True
        },
        'tests': [
            {
                'path_name': 'forge_auth:groups-list',
                'fixture': {
                    'object_name': 'group',
                    'model': Group,
                },
                'expected_responses': {
                    200: {'expected_fields': ['0.pk', '0.name', '0.permissions']},
                },
                'method': 'GET',

            },
            {
                'path_name': 'forge_auth:groups-detail',
                'method': 'GET',
                'reverse_params': {
                    "kwargs": {'pk': 'self.group.pk'}
                },
                'fixture': {
                    'object_name': 'group',
                    'model': Group,
                },
                'expected_responses': {
                    200: {
                        'expected_fields': ['pk', 'name', 'permissions'],
                        'expected_type_of_fields': {'pk': int},
                        'expected_response': dict
                    },
                },

            }
        ]
    }


class UserTestCase(ForgeCase):
    config: ConfigForgeCase = {
        'factory_params': {
            'max_depth': 7,
            'create_m2m': True
        },
        'tests': [
            {
                'test_name': 'users_list',
                'path_name': 'forge_auth:users-list',
                'fixture': {
                    'object_name': 'user',
                    'model': User,
                },
                'expected_responses': {
                    200: {
                        'authenticated': True,
                        'expected_fields': ['results', 'results.0.pk', 'results.0.first_name', 'results.0.last_name', 'results.0.groups']
                    },
                    401: {
                        'authenticated': False
                    },
                },
                'method': 'GET',
            },
            {
                'method': 'GET',
                'path_name': 'forge_auth:users-detail',
                'reverse_params': {
                    'kwargs': {'pk': 'self.user.pk'}
                },
                'fixture': {
                    'object_name': 'user',
                    'model': User,
                },
                'expected_responses': {
                    200: {
                        'authenticated': True,
                        'expected_fields': ['pk', 'first_name', 'last_name', 'groups'],
                        'expected_type_of_fields': {'pk': int},
                        'expected_response': dict
                    },
                    401: {
                        'authenticated': False
                    },
                    404: {
                        'authenticated': True,
                        'reverse_params': {
                            'kwargs': {'pk': 9999999}
                        }
                    }
                },
            },
            {
                'method': 'POST',
                'path_name': 'forge_auth:users-list',
                'http_client_params': {
                    'fixture': {
                        'model': User,
                        'fields': ['first_name', 'last_name', 'phone_number', 'email']
                    },
                    'content_type': 'application/json',
                },
                'expected_responses': {
                    201: {
                        'expected_fields': ['pk', 'first_name', 'last_name', 'phone_number', 'email'],
                        'expected_type_of_fields': {'pk': int},
                        'expected_response': dict
                    },
                    400: {
                        'http_client_params': {
                            'fixture': {
                                'model': User,
                                'fields': ['first_name', 'last_name']
                            }
                        },
                    },
                }

            },
            {
                'method': 'PATCH',
                'path_name': 'forge_auth:users-detail',
                'reverse_params': {
                    'kwargs': {'pk': 'self.user.pk'}
                },
                'fixture': {
                    'object_name': 'user',
                    'model': User,
                },
                'expected_responses': {
                    200: {
                        'authenticated': True,
                        'http_client_params': {
                            'fixture': {
                                'data': {
                                    'first_name': 'new_first_name',
                                    'last_name': 'new_last_name'
                                }
                            },
                        },
                        'expected_value_of_fields': {
                            'first_name': 'new_first_name',
                            'last_name': 'new_last_name'
                        }
                    },
                    401: {
                        'authenticated': False
                    },
                    404: {
                        'authenticated': True,
                        'reverse_params': {
                            'kwargs': {'pk': 9999999}
                        }
                    }
                }
            },
            {
                'method': 'DELETE',
                'path_name': 'forge_auth:users-detail',
                'reverse_params': {
                    'kwargs': {'pk': 'self.user.pk'}
                },
                'expected_responses': {
                    204: {'authenticated': True},
                    401: {'authenticated': False},
                    404: {
                        'authenticated': True,
                        'reverse_params': {
                            'kwargs': {'pk': 9999999}
                        }
                    }
                }
            },
            {
                'method': 'POST',
                'path_name': 'forge_auth:users-verify-email',
                'fixture': {
                    'model': User,
                    'object_name': 'user_test',
                    'kwargs': {'email': 'test@test.com'}
                },
                'expected_responses': {
                    200: {
                        'authenticated': True,
                        'http_client_params': {
                            'fixture': {
                                'data': { 'verify' : 'test@test.com'}
                            }
                        },
                        'expected_response': dict,
                        'expected_type_of_fields': {'exists': bool},
                        'expected_value_of_fields': {'exists': True}
                    },
                    400: {'authenticated': True},
                }
            },
            {
                'method': 'POST',
                'path_name': 'forge_auth:users-verify-phone',
                'fixture': {
                    'model': User,
                    'object_name': 'user_test',
                    'kwargs': {'phone_number': '0000000000'}
                },
                'expected_responses': {
                    200: {
                        'authenticated': True,
                        'http_client_params': {
                            'fixture': {
                                'data': { 'verify' : '0000000000'}
                            }
                        },
                        'expected_response': dict,
                        'expected_type_of_fields': {'exists': bool},
                        'expected_value_of_fields': {'exists': True}
                    },
                    400: {'authenticated': True},
                }
            },
            {
                'method': 'GET',
                'path_name': 'forge_auth:users-current',
                'fixture': {
                    'model': User,
                    'object_name': 'user_test',
                    'kwargs': {'phone_number': '0000000000'}
                },
                'expected_responses': {
                    200: {
                        'authenticated': True,
                        'expected_response': dict,
                    },
                    401: {'authenticated': False},
                }
            },
            {
                'method': 'POST',
                'path_name': 'forge_auth:users-logout',
                'fixture': {
                    'model': User,
                    'object_name': 'user_test',
                    'kwargs': {'phone_number': '0000000000'}
                },
                'expected_responses': {
                    204: {
                        'authenticated': True,
                    },
                    401: {'authenticated': False},
                }
            },
            {
                'method': 'GET',
                'path_name': 'forge_auth:users-session-check',
                'fixture': {
                    'model': User,
                    'object_name': 'user_test',
                    'kwargs': {'phone_number': '0000000000'}
                },
                'expected_responses': {
                    200: {
                        'authenticated': True,
                    },
                    401: {'authenticated': False},
                }
            },
        ]
    }


    def test_login_success(self):
        kwargs_user = self.factory.build_create_kwargs(User)
        kwargs_user.pop('phone_number')
        user = User.objects.create(phone_number='+225000000023', **kwargs_user)
        user.set_password('qwerty123')
        user.save()
        user.refresh_from_db()
        url = reverse('forge_auth:users-login')
        response_otp = self.client.post(reverse('forge_auth:users-obtain-otp'), {'username': self.user.username}, format='json')
        self.assertEqual(response_otp.status_code, 200)
        response = self.client.post(url, {'username': self.user.username, 'code': '123456'}, format='json')
        self.assertEqual(response.status_code, 401)

    def test_login_with_register_include_otp_ask_true_success(self):
        url = reverse('forge_auth:users-login')
        response_otp = self.client.post(reverse('forge_auth:users-obtain-otp'), {'username': self.user.username}, format='json')
        self.assertEqual(response_otp.status_code, 200)
        response = self.client.post(url, {'username': self.user.username, 'code': '123456'}, format='json')
        self.assertEqual(response.status_code, 401)
    
    def test_login_failure_data_not_found(self):
        url = reverse('forge_auth:users-login')
        response = self.client.post(url, {}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_refresh_success(self):
        from rest_framework_simplejwt.tokens import RefreshToken
        token = RefreshToken.for_user(self.user)
        access = str(token.access_token)
        refresh = str(token)
        self.client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {access}"
        

        url = reverse('forge_auth:users-refresh')
        response = self.client.post(url, {'refresh': str(token)},  format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(type(response.data), dict)
        self.assertTrue('access' in response.data)
        self.assertTrue('refresh' in response.data)


