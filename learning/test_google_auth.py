from urllib.parse import parse_qs, urlparse
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from allauth.socialaccount.models import SocialAccount, SocialLogin
from django.test import RequestFactory
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.messages.storage.fallback import FallbackStorage


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class GoogleLoginTests(TestCase):
    def test_hidden_without_credentials(self):
        with override_settings(GOOGLE_LOGIN_ENABLED=False):
            self.assertNotContains(self.client.get(reverse('login')), 'Continue with Google')

    @override_settings(GOOGLE_LOGIN_ENABLED=True)
    def test_both_account_pages_offer_post_button(self):
        for page in ['login', 'register']:
            response = self.client.get(reverse(page))
            self.assertContains(response, 'Continue with Google')
            self.assertContains(response, 'action="/accounts/google/login/"')

    @override_settings(SOCIALACCOUNT_PROVIDERS={'google': {'APP': {'client_id': 'test-client', 'secret': 'test-secret', 'key': ''}, 'OAUTH_PKCE_ENABLED': True}})
    def test_start_uses_state_pkce_and_callback(self):
        response = self.client.post(reverse('google_login'))
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response.url).query)
        self.assertEqual(query['client_id'], ['test-client'])
        self.assertTrue(query['state'][0])
        self.assertTrue(query['code_challenge'][0])
        self.assertEqual(query['redirect_uri'], ['http://testserver/accounts/google/login/callback/'])

    def test_csrf_required(self):
        from django.test import Client
        self.assertEqual(Client(enforce_csrf_checks=True).post(reverse('google_login')).status_code, 403)

    def test_existing_password_account_still_logs_in(self):
        get_user_model().objects.create_user(username='sailor', password='SafeTestPassword991!')
        response = self.client.post(reverse('login'), {'username': 'sailor', 'password': 'SafeTestPassword991!'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('_auth_user_id', self.client.session)

    def test_email_does_not_automatically_link_existing_account(self):
        from allauth.account.models import EmailAddress
        from django.contrib.auth.models import AnonymousUser
        user = get_user_model().objects.create_user(username='existing', email='same@example.com')
        request = RequestFactory().get('/')
        SessionMiddleware(lambda r: None).process_request(request)
        request.user = AnonymousUser()
        request._messages = FallbackStorage(request)
        social = SocialLogin(user=get_user_model()(email=user.email), account=SocialAccount(provider='google', uid='new-google-id'), email_addresses=[EmailAddress(email=user.email, verified=True, primary=True)])
        from allauth.socialaccount.providers.google.provider import GoogleProvider
        from allauth.socialaccount.models import SocialApp
        social.provider = GoogleProvider(request, app=SocialApp(provider="google", client_id="test", secret="test"))
        social.lookup()
        self.assertFalse(social.is_existing)
        self.assertFalse(SocialAccount.objects.filter(user=user).exists())
