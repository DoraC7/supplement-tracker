"""No network calls or real credentials: authentication must fail closed."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from streamlit.testing.v1 import AppTest

from supplemind.cloud import AuthenticationError, CloudConfig, OwnerAuth

OWNER = "11111111-1111-4111-8111-111111111111"
CONFIG = CloudConfig("https://example.supabase.co", "test-public-key", OWNER,
                     "postgresql://test:fake@localhost/test")
SECRETS = {"cloud": {"supabase_url": CONFIG.url, "publishable_key": CONFIG.publishable_key,
                     "owner_user_id": OWNER, "database_url": CONFIG.database_url}}


def response(data, code=200):
    result = Mock(status_code=code)
    result.json.return_value = data
    return result


class AuthTests(unittest.TestCase):
    def test_missing_configuration_and_invalid_owner_are_rejected(self):
        for values in ({}, {"cloud": {}}, {"cloud": {**SECRETS["cloud"], "owner_user_id": "bad"}}):
            with self.assertRaises(AuthenticationError):
                CloudConfig.load(values)
        self.assertEqual(CloudConfig.load(SECRETS), CONFIG)
        self.assertNotIn("fake", repr(CONFIG))

    def test_auth_endpoint_cannot_be_redirected(self):
        for url in ("http://example.supabase.co", "https://example.supabase.co.attacker.test",
                    "https://example.supabase.co/path", "https://u:p@example.supabase.co"):
            with self.assertRaises(AuthenticationError):
                CloudConfig.load({"cloud": {**SECRETS["cloud"], "supabase_url": url}})

    @patch("supplemind.cloud.requests.request")
    def test_only_verified_owner_is_authorized(self, request):
        auth = OwnerAuth(CONFIG)
        for user in ({"id": "other", "email_confirmed_at": "now"}, {"id": OWNER}):
            request.return_value = response(user)
            with self.assertRaises(AuthenticationError):
                auth.verify("test-token")
        request.return_value = response({"id": OWNER, "email_confirmed_at": "now"})
        self.assertEqual(auth.verify("test-token")["id"], OWNER)
        self.assertFalse(request.call_args.kwargs["allow_redirects"])

    @patch("supplemind.cloud.requests.request")
    def test_login_checks_user_endpoint_not_only_token_response(self, request):
        request.side_effect = [response({"access_token": "test-token"}),
                               response({"id": "other", "email_confirmed_at": "now"})]
        with self.assertRaises(AuthenticationError):
            OwnerAuth(CONFIG).sign_in("person@example.com", "fake-password")
        self.assertEqual(request.call_count, 2)

    @patch("supplemind.cloud.requests.request")
    def test_expired_token_outage_and_redirect_all_deny_access(self, request):
        for code in (401, 403, 302, 500):
            request.return_value = response({}, code)
            with self.assertRaises(AuthenticationError):
                OwnerAuth(CONFIG).verify("test-token")
        request.side_effect = requests.ConnectionError("fake-secret")
        with self.assertRaises(AuthenticationError) as caught:
            OwnerAuth(CONFIG).verify("test-token")
        self.assertNotIn("fake-secret", str(caught.exception))

    def test_no_config_stops_before_sqlite_or_postgres(self):
        with patch("supplemind.cloud.CloudConfig.load", side_effect=AuthenticationError("尚未設定")), \
                patch("supplemind.ui.Tracker") as local, patch("supplemind.postgres.PostgresTracker") as remote:
            app = AppTest.from_string("from supplemind.ui import main\nmain(cloud=True)").run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.tabs), 0)
            local.assert_not_called()
            remote.assert_not_called()

    def test_login_form_and_password_cleanup(self):
        with patch("supplemind.cloud.CloudConfig.load", return_value=CONFIG), \
                patch("supplemind.cloud.OwnerAuth.sign_in", side_effect=AuthenticationError("登入失敗")), \
                patch("supplemind.postgres.PostgresTracker") as remote, patch("supplemind.ui.Tracker") as local:
            app = AppTest.from_string("from supplemind.ui import main\nmain(cloud=True)").run()
            self.assertFalse(app.exception)
            app.text_input(key="cloud_email").set_value("person@example.com")
            app.text_input(key="cloud_password").set_value("fake-password")
            app.button[0].click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.text_input(key="cloud_password").value, "")
            self.assertEqual(len(app.tabs), 0)
            remote.assert_not_called()
            local.assert_not_called()

    def test_authorized_owner_reaches_app_and_logout_clears_session(self):
        from supplemind.tracker import Tracker
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "isolated.db")
            with patch("supplemind.cloud.CloudConfig.load", return_value=CONFIG), \
                    patch("supplemind.cloud.OwnerAuth.verify", return_value={"id": OWNER}), \
                    patch("supplemind.cloud.OwnerAuth.sign_in", return_value="test-token"), \
                    patch("supplemind.cloud.OwnerAuth.sign_out") as logout, \
                    patch("supplemind.postgres.PostgresTracker", side_effect=lambda _: Tracker(path)):
                app = AppTest.from_string("from supplemind.ui import main\nmain(cloud=True)").run()
                app.text_input(key="cloud_email").set_value("person@example.com")
                app.text_input(key="cloud_password").set_value("fake-password")
                app.button[0].click().run()
                self.assertFalse(app.exception)
                self.assertEqual(len(app.tabs), 5)
                app.button(key="cloud_logout").click().run()
                self.assertFalse(app.exception)
                self.assertEqual(len(app.tabs), 0)
                logout.assert_called_once_with("test-token")