"""Fail-closed, single-owner Supabase Auth gate for the cloud entry point.

Never cache clients or user data globally. Tokens live only in the current
Streamlit session; passwords are removed after each attempt. No service-role
API key is needed. The database credential is separate and server-side only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse
from uuid import UUID

import requests
import streamlit as st

from .errors import SupplementError


class AuthenticationError(SupplementError):
    pass


@dataclass(frozen=True)
class CloudConfig:
    url: str
    publishable_key: str = field(repr=False)
    owner_id: str
    database_url: str = field(repr=False)

    @classmethod
    def load(cls, secrets):
        try:
            values = secrets["cloud"]
            url = str(values["supabase_url"]).rstrip("/")
            parsed = urlparse(url)
            if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".supabase.co"):
                raise ValueError()
            if parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password or parsed.port:
                raise ValueError()
            owner = str(UUID(str(values["owner_user_id"])))
            key = str(values["publishable_key"]).strip()
            dsn = str(values["database_url"]).strip()
            if not key or not dsn.startswith(("postgres://", "postgresql://")):
                raise ValueError()
            if key.startswith("sb_secret_"):
                raise ValueError()
            return cls(url, key, owner, dsn)
        except Exception:
            raise AuthenticationError("雲端設定尚未完成，請由管理者設定 Secrets。為保護資料，目前停止存取。") from None


class OwnerAuth:
    def __init__(self, config: CloudConfig):
        self.config = config

    def _request(self, method, path, *, token=None, payload=None):
        headers = {"apikey": self.config.publishable_key}
        if token:
            headers["Authorization"] = "Bearer " + token
        try:
            response = requests.request(method, self.config.url + "/auth/v1/" + path,
                                        headers=headers, json=payload, timeout=15,
                                        allow_redirects=False)
            if response.status_code != 200:
                raise ValueError()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except Exception:
            raise AuthenticationError("無法驗證登入。請確認帳號密碼或網路連線後再試。") from None

    def verify(self, token):
        if not isinstance(token, str) or not token:
            raise AuthenticationError("請先登入。")
        user = self._request("GET", "user", token=token)
        if user.get("id") != self.config.owner_id or not user.get("email_confirmed_at"):
            raise AuthenticationError("此帳號未獲授權使用這個私人 App。")
        return user

    def sign_in(self, email, password):
        data = self._request("POST", "token?grant_type=password",
                             payload={"email": email.strip(), "password": password})
        token = data.get("access_token")
        self.verify(token)
        return token

    def sign_out(self, token):
        if token:
            try:
                requests.post(self.config.url + "/auth/v1/logout?scope=local",
                              headers={"apikey": self.config.publishable_key,
                                       "Authorization": "Bearer " + token},
                              timeout=10, allow_redirects=False)
            except requests.RequestException:
                pass  # Local session is always cleared even if Auth is offline.


def login_panel(config):
    auth = OwnerAuth(config)
    if "cloud_token" in st.session_state:
        try:
            auth.verify(st.session_state["cloud_token"])
        except AuthenticationError as exc:
            st.session_state.pop("cloud_token", None)
            st.warning(str(exc))
        else:
            if st.button("登出", key="cloud_logout"):
                auth.sign_out(st.session_state.pop("cloud_token", None))
                st.session_state.clear()
                st.rerun()
            st.caption("私人雲端版本 · 台北時間 · 僅授權帳號可使用")
            return
    st.markdown("### 登入你的保健日常")
    st.caption("這是私人保健品記錄空間，只允許管理者預先建立的帳號登入。")

    def submit():
        password = st.session_state.pop("cloud_password", "")
        try:
            token = auth.sign_in(st.session_state.get("cloud_email", ""), password)
            st.session_state["cloud_token"] = token
            st.session_state.pop("cloud_login_error", None)
        except AuthenticationError as exc:
            st.session_state["cloud_login_error"] = str(exc)
        finally:
            password = None

    with st.form("cloud_login"):
        st.text_input("電子郵件", key="cloud_email", autocomplete="username")
        st.text_input("密碼", type="password", key="cloud_password", autocomplete="current-password")
        st.form_submit_button("登入", type="primary", width="stretch", on_click=submit)
    if "cloud_login_error" in st.session_state:
        st.error(st.session_state["cloud_login_error"])
    st.caption("登入過期或重新開啟瀏覽器時需再次登入。忘記密碼請由管理者在 Supabase 重設。")
    st.stop()


def open_cloud_tracker(config):
    # Also called on every timed fragment rerun, not just the main page render.
    try:
        OwnerAuth(config).verify(st.session_state.get("cloud_token"))
    except AuthenticationError:
        st.session_state.pop("cloud_token", None)
        st.rerun(scope="app")
    from .postgres import PostgresTracker
    return PostgresTracker(config.database_url)