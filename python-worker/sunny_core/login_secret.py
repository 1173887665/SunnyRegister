from __future__ import annotations

import json
import random
import secrets
import time
from typing import Any, Callable

from .auth_challenges import generate_totp
from .browser_backend import open_registration_browser
from .browser_traffic import BrowserTrafficOptimizer, ProxyTrafficMeter
from .mailbox import MailAccount, create_mailbox_reader
from .openai_auth import CHATGPT_BASE_URL, generate_register_fingerprint


class MFAReauthenticationRequired(RuntimeError):
    pass


def generate_chatgpt_password(length: int = 16) -> str:
    length = max(12, int(length or 16))
    groups = (
        "ABCDEFGHJKLMNPQRSTUVWXYZ",
        "abcdefghijkmnopqrstuvwxyz",
        "23456789",
        "!@#$%^&*?_-+=",
    )
    chars = [secrets.choice(group) for group in groups]
    pool = "".join(groups)
    chars.extend(secrets.choice(pool) for _ in range(length - len(chars)))
    random.SystemRandom().shuffle(chars)
    return "".join(chars)


class LoginSecretSetupFlow:
    def __init__(
        self,
        account: MailAccount,
        session: dict[str, Any],
        proxy_url: str,
        log: Callable[[str], None] | None = None,
        *,
        should_cancel: Callable[[], bool] | None = None,
        mailbox_proxy_url: str | None = None,
        traffic_meter: ProxyTrafficMeter | None = None,
        on_progress: Callable[[str], None] | None = None,
    ):
        self.account = account
        self.session = dict(session or {})
        self.proxy_url = str(proxy_url or "")
        self.mailbox_proxy_url = self.proxy_url if mailbox_proxy_url is None else str(mailbox_proxy_url or "")
        self.log = log or (lambda _message: None)
        self.should_cancel = should_cancel or (lambda: False)
        self.traffic_meter = traffic_meter
        self.on_progress = on_progress or (lambda _checkpoint: None)
        self.traffic_optimizer = BrowserTrafficOptimizer(traffic_meter) if traffic_meter is not None else None
        self.reader: Any | None = None

    def _check_cancelled(self) -> None:
        if self.should_cancel():
            from .openai_auth import TaskCancelledError

            raise TaskCancelledError("Task cancelled by user")

    def _sleep(self, seconds: float) -> None:
        deadline = time.time() + max(0.0, seconds)
        while time.time() < deadline:
            self._check_cancelled()
            time.sleep(min(0.5, deadline - time.time()))

    def _storage_state(self) -> dict[str, Any]:
        state = self.session.get("storage_state_json")
        if isinstance(state, str):
            try:
                state = json.loads(state)
            except (TypeError, ValueError):
                state = {}
        if not isinstance(state, dict) or not isinstance(state.get("cookies"), list):
            raise RuntimeError("当前账户没有可复用的 ChatGPT 浏览器登录态")
        return state

    def _reader_instance(self):
        if self.reader is None:
            self.reader = create_mailbox_reader(self.account, self.log, self.mailbox_proxy_url)
            self.reader.connect()
        return self.reader

    @staticmethod
    def _session_json(page) -> dict[str, Any]:
        result = page.evaluate(
            """async () => {
                const response = await fetch('https://chatgpt.com/api/auth/session', {credentials:'include'});
                const text = await response.text();
                let data = null;
                try { data = JSON.parse(text); } catch (_) {}
                return {ok:response.ok, status:response.status, data, text};
            }"""
        )
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, dict) or not (data.get("accessToken") or data.get("access_token")):
            raise RuntimeError(f"ChatGPT 登录态已失效: HTTP {result.get('status') if isinstance(result, dict) else 0}")
        return data

    @staticmethod
    def _page_state(page) -> dict[str, Any]:
        try:
            return page.evaluate(
                r"""() => ({
                    url: location.href,
                    text: (document.body?.innerText || '').replace(/\s+/g, ' ').slice(0, 1400),
                    passwordInputs: [...document.querySelectorAll('input[type="password"],input[autocomplete="new-password"]')]
                        .filter(el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) && !el.disabled).length,
                    codeInputs: [...document.querySelectorAll('input[autocomplete="one-time-code"],input[name*="code" i],input[inputmode="numeric"]')]
                        .filter(el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) && !el.disabled).length,
                })"""
            )
        except Exception:
            return {"url": str(getattr(page, "url", "") or ""), "text": "", "passwordInputs": 0, "codeInputs": 0}

    @staticmethod
    def _click_password_action(page) -> dict[str, Any]:
        try:
            return page.evaluate(
            r"""() => {
                const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
                    && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
                    && !el.disabled && String(el.getAttribute('aria-disabled') || '').toLowerCase() !== 'true';
                const desc = el => [el.innerText, el.textContent, el.getAttribute('aria-label'), el.title,
                    el.getAttribute('data-testid'), el.getAttribute('data-dd-action-name'), el.id, el.name]
                    .filter(Boolean).join(' ').replace(/\s+/g, ' ').trim().toLowerCase();
                const password = /password|密码|パスワード|비밀번호/;
                const action = /add|create|set|update|change|manage|添加|创建|设置|更新|更改|管理|追加|変更|설정|변경/;
                const items = [...document.querySelectorAll('button,a,[role="button"],[role="link"],[role="tab"]')].filter(visible);
                const hit = items.find(el => {
                    const own = desc(el);
                    if (password.test(own) && action.test(own)) return true;
                    if (!password.test(own)) return false;
                    const parent = el.closest('li,section,form,[role="dialog"],div');
                    return action.test(desc(parent || el));
                }) || items.find(el => /password/.test(String(el.getAttribute('data-testid') || '').toLowerCase()));
                if (!hit) return {ok:false, reason:'password_action_missing', samples:items.map(desc).filter(Boolean).slice(0,40)};
                hit.scrollIntoView({block:'center'}); hit.click();
                return {ok:true, detail:desc(hit).slice(0,180)};
            }"""
            ) or {"ok": False, "reason": "empty_result"}
        except Exception as exc:
            return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}

    @staticmethod
    def _click_settings_navigation(page, step: str) -> bool:
        try:
            return bool(page.evaluate(
                r"""step => {
                    const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
                        && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
                    const enabled = el => !el.disabled && String(el.getAttribute('aria-disabled') || '').toLowerCase() !== 'true';
                    const desc = el => [el.innerText, el.textContent, el.getAttribute('aria-label'), el.title,
                        el.getAttribute('data-testid'), el.getAttribute('data-dd-action-name')]
                        .filter(Boolean).join(' ').replace(/\s+/g, ' ').trim().toLowerCase();
                    const items = [...document.querySelectorAll('button,a,[role="button"],[role="link"],[role="tab"]')]
                        .filter(el => visible(el) && enabled(el));
                    const patterns = {
                        account: /^(account|账户|アカウント|계정)$/,
                        settings: /settings|设置|設定|설정/,
                        profile: /profile|account menu|user menu|个人资料|账户菜单|プロフィール|프로필/
                    };
                    const hit = items.find(el => patterns[String(step || '')]?.test(desc(el)));
                    if (!hit) return false;
                    hit.scrollIntoView({block:'center'}); hit.click(); return true;
                }""",
                step,
            ))
        except Exception:
            return False

    @staticmethod
    def _add_password_via_protocol(page, password: str) -> dict[str, Any]:
        """Add a password through the authenticated OpenAI account endpoint.

        The reset-password page accepts the existing browser login state and is
        more stable than depending on the ChatGPT settings SPA's button labels.
        """
        try:
            page.goto("https://auth.openai.com/reset-password/new-password", wait_until="domcontentloaded", timeout=60000)
            return page.evaluate(
                r"""async password => {
                    const response = await fetch('https://auth.openai.com/api/accounts/password/add', {
                        method: 'POST', credentials: 'include',
                        headers: {'accept':'application/json', 'content-type':'application/json'},
                        body: JSON.stringify({password})
                    });
                    const text = await response.text();
                    let data = null; try { data = JSON.parse(text); } catch (_) {}
                    return {ok: response.ok && (!data || data.success !== false), status: response.status, data, text: text.slice(0, 500)};
                }""",
                password,
            ) or {"ok": False, "reason": "empty_result"}
        except Exception as exc:
            return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}

    def _progress(self, checkpoint: str) -> None:
        try:
            self.on_progress(checkpoint)
        except Exception:
            pass

    @staticmethod
    def _submit_password(page, password: str) -> bool:
        return bool(page.evaluate(
            r"""password => {
                const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
                    && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
                    && !el.disabled && !el.readOnly;
                const inputs = [...document.querySelectorAll('input[type="password"],input[autocomplete="new-password"]')].filter(visible);
                if (!inputs.length) return false;
                for (const input of inputs) {
                    input.focus();
                    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
                    if (setter) setter.call(input, password); else input.value = password;
                    input.dispatchEvent(new Event('input', {bubbles:true}));
                    input.dispatchEvent(new Event('change', {bubbles:true}));
                }
                const scope = inputs[0].closest('form') || inputs[0].closest('[role="dialog"]') || document;
                const buttons = [...scope.querySelectorAll('button,input[type="submit"],[role="button"]')].filter(visible);
                const desc = el => [el.innerText, el.textContent, el.value, el.getAttribute('aria-label')].filter(Boolean).join(' ').toLowerCase();
                const submit = buttons.find(el => /save|continue|submit|update|change|set|保存|继续|提交|更新|更改|设置|続行|確認/.test(desc(el)))
                    || buttons.find(el => String(el.type || '').toLowerCase() === 'submit');
                if (!submit) return false;
                submit.scrollIntoView({block:'center'}); submit.click(); return true;
            }""",
            password,
        ))

    @staticmethod
    def _fill_code(page, code: str) -> bool:
        return bool(page.evaluate(
            r"""code => {
                const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) && !el.disabled;
                const inputs = [...document.querySelectorAll('input[autocomplete="one-time-code"],input[name*="code" i],input[inputmode="numeric"]')].filter(visible);
                if (!inputs.length) return false;
                if (inputs.length === 1) {
                    inputs[0].focus(); inputs[0].value = code;
                    inputs[0].dispatchEvent(new Event('input',{bubbles:true}));
                    inputs[0].dispatchEvent(new Event('change',{bubbles:true}));
                } else {
                    [...code].forEach((digit,index) => { if (inputs[index]) { inputs[index].focus(); inputs[index].value=digit; inputs[index].dispatchEvent(new Event('input',{bubbles:true})); } });
                }
                const scope = inputs[0].closest('form') || document;
                const submit = [...scope.querySelectorAll('button,input[type="submit"],[role="button"]')].find(el => visible(el)
                    && /continue|verify|submit|继续|验证|提交|続行|確認/i.test(`${el.innerText||''} ${el.value||''} ${el.getAttribute('aria-label')||''}`));
                submit?.click(); return true;
            }""",
            code,
        ))

    def _complete_reauthentication(self, page, min_timestamp: float, password: str) -> None:
        deadline = time.time() + 150
        email_code_used = False
        totp_used = False
        password_used = False
        while time.time() < deadline:
            self._check_cancelled()
            url = str(page.url or "").lower()
            if "chatgpt.com" in url:
                try:
                    self._session_json(page)
                    return
                except Exception:
                    pass
            state = self._page_state(page)
            if state.get("passwordInputs") and not password_used:
                if not password or not self._submit_password(page, password):
                    raise RuntimeError("重认证要求密码，但密码输入未能提交")
                password_used = True
                self._sleep(2)
                continue
            if state.get("codeInputs"):
                is_totp = "mfa" in url or "authenticator" in str(state.get("text") or "").lower()
                if is_totp and not totp_used:
                    if not self.account.totp_secret:
                        raise RuntimeError("重认证要求 TOTP，但账户没有 2FA 密钥")
                    if not self._fill_code(page, generate_totp(self.account.totp_secret)):
                        raise RuntimeError("TOTP 重认证输入失败")
                    totp_used = True
                    self._sleep(2)
                    continue
                if not email_code_used:
                    code = self._reader_instance().wait_for_code(min_timestamp)
                    if not self._fill_code(page, code):
                        raise RuntimeError("邮箱重认证验证码输入失败")
                    email_code_used = True
                    self._sleep(2)
                    continue
            self._sleep(0.75)
        raise TimeoutError(f"ChatGPT 重认证超时: {self._page_state(page)}")

    def _add_password(self, page) -> str:
        password = generate_chatgpt_password()
        protocol_result = self._add_password_via_protocol(page, password)
        if protocol_result.get("ok"):
            self.log("[登录密钥] 已通过 OpenAI 协议接口添加 ChatGPT 密码（内容不写日志）")
            return password
        self.log(
            "[登录密钥] 协议添加密码接口未完成，将回退账户设置页："
            f"HTTP {protocol_result.get('status', 0)} {protocol_result.get('reason', '')}".strip()
        )
        page.goto(f"{CHATGPT_BASE_URL}/#settings/Account", wait_until="domcontentloaded", timeout=60000)
        self._sleep(2)
        deadline = time.time() + 60
        navigation_steps = ("account", "settings", "profile")
        navigation_index = 0
        last_result: dict[str, Any] = {}
        while time.time() < deadline:
            self._check_cancelled()
            result = self._click_password_action(page)
            last_result = result if isinstance(result, dict) else {"ok": bool(result)}
            if last_result.get("ok"):
                break
            if navigation_index < len(navigation_steps) and self._click_settings_navigation(page, navigation_steps[navigation_index]):
                navigation_index += 1
            self._sleep(1)
        else:
            samples = " | ".join(str(item) for item in (last_result.get("samples") or [])[:8])
            detail = f"；可见控件: {samples}" if samples else ""
            raise RuntimeError(f"账户设置中未找到添加密码入口{detail}")
        submitted = False
        disappeared_at = 0.0
        otp_min_timestamp = time.time() - 5
        while time.time() < deadline + 120:
            state = self._page_state(page)
            url = str(state.get("url") or "").lower()
            if "auth.openai.com" in url and (state.get("codeInputs") or state.get("passwordInputs")):
                self._complete_reauthentication(page, otp_min_timestamp, password)
                continue
            if state.get("passwordInputs") and self._submit_password(page, password):
                submitted = True
                self.log("[登录密钥] 已提交新 ChatGPT 密码（内容不写日志）")
                self._sleep(2)
                continue
            if submitted and not state.get("passwordInputs"):
                if not disappeared_at:
                    disappeared_at = time.time()
                elif time.time() - disappeared_at >= 3:
                    return password
            self._sleep(0.75)
        raise TimeoutError(f"添加 ChatGPT 密码超时: {self._page_state(page)}")

    def _reauth_for_2fa(self, page, password: str) -> dict[str, Any]:
        page.goto(CHATGPT_BASE_URL, wait_until="domcontentloaded", timeout=60000)
        payload = page.evaluate(
            """async ({email}) => {
                const csrfResponse = await fetch('/api/auth/csrf', {credentials:'include'});
                if (!csrfResponse.ok) return {ok:false,status:csrfResponse.status};
                const csrf = await csrfResponse.json();
                const query = new URLSearchParams({connection:'password',login_hint:email,reauth:'password',max_age:'0'});
                const body = new URLSearchParams({callbackUrl:'https://chatgpt.com/?action=enable&factor=totp',csrfToken:csrf.csrfToken,json:'true'});
                const response = await fetch('/api/auth/signin/openai?' + query.toString(), {
                    method:'POST',credentials:'include',headers:{'content-type':'application/x-www-form-urlencoded'},body:body.toString()
                });
                const text = await response.text();
                let data={}; try { data=JSON.parse(text); } catch (_) {}
                return {ok:response.ok,status:response.status,data};
            }""",
            {"email": self.account.email},
        )
        auth_url = str(((payload or {}).get("data") or {}).get("url") or "")
        if not payload.get("ok") or not auth_url:
            raise RuntimeError(f"发起 2FA 重认证失败: HTTP {payload.get('status')}")
        min_timestamp = time.time() - 5
        page.goto(auth_url, wait_until="domcontentloaded", timeout=60000)
        self._complete_reauthentication(page, min_timestamp, password)
        page.goto(CHATGPT_BASE_URL, wait_until="domcontentloaded", timeout=60000)
        return self._session_json(page)

    @staticmethod
    def _mfa_info(page, access_token: str) -> dict[str, Any]:
        return page.evaluate(
            """async token => {
                const headers = {'accept':'application/json'};
                if (token) headers.authorization = 'Bearer ' + token;
                const response = await fetch('/backend-api/accounts/mfa_info', {credentials:'include',headers});
                const text=await response.text(); let data={}; try { data=JSON.parse(text); } catch (_) {}
                return {ok:response.ok,status:response.status,data,text:text.slice(0,500)};
            }""",
            access_token,
        )

    @staticmethod
    def _enroll_totp(page, access_token: str) -> dict[str, Any]:
        return page.evaluate(
            """async token => {
                const headers = {'accept':'application/json','content-type':'application/json'};
                if (token) headers.authorization = 'Bearer ' + token;
                const response = await fetch('/backend-api/accounts/mfa/enroll', {
                    method:'POST',credentials:'include',headers,body:JSON.stringify({factor_type:'totp'})
                });
                const text=await response.text(); let data={}; try { data=JSON.parse(text); } catch (_) {}
                return {ok:response.ok,status:response.status,data,text:text.slice(0,500)};
            }""",
            access_token,
        )

    @staticmethod
    def _activate_totp(page, access_token: str, code: str, session_id: str) -> dict[str, Any]:
        return page.evaluate(
            """async ({token,code,sessionId}) => {
                const headers = {'accept':'application/json','content-type':'application/json'};
                if (token) headers.authorization = 'Bearer ' + token;
                const response = await fetch('/backend-api/accounts/mfa/user/activate_enrollment', {
                    method:'POST',credentials:'include',headers,
                    body:JSON.stringify({code,factor_type:'totp',session_id:sessionId})
                });
                const text=await response.text(); let data={}; try { data=JSON.parse(text); } catch (_) {}
                return {ok:response.ok,status:response.status,data,text:text.slice(0,500)};
            }""",
            {"token": access_token, "code": code, "sessionId": session_id},
        )

    @staticmethod
    def _totp_factors(info: dict[str, Any]) -> list[dict[str, Any]]:
        data = info.get("data") if isinstance(info, dict) else {}
        factors = (data or {}).get("factors") if isinstance(data, dict) else {}
        items = (factors or {}).get("totp") if isinstance(factors, dict) else []
        return [item for item in (items or []) if isinstance(item, dict)]

    def _fresh_totp_code(self, secret: str, *, force_next_window: bool = False) -> str:
        remaining = 30 - (time.time() % 30)
        if force_next_window or remaining <= 5:
            self._sleep(remaining + 0.25)
        return generate_totp(secret)

    @staticmethod
    def _require_mfa_response(result: dict[str, Any], operation: str) -> None:
        status = int(result.get("status") or 0) if isinstance(result, dict) else 0
        if status in {401, 403}:
            raise MFAReauthenticationRequired(f"{operation}要求重新认证: HTTP {status}")
        if not isinstance(result, dict) or not result.get("ok"):
            raise RuntimeError(f"{operation}失败: HTTP {status}")

    def _setup_2fa_protocol(self, page, access_token: str) -> tuple[str, dict[str, Any]]:
        info_before = self._mfa_info(page, access_token)
        self._require_mfa_response(info_before, "查询 2FA 状态")
        info_data = info_before.get("data") or {}
        if not isinstance(info_data, dict):
            raise RuntimeError("查询 2FA 状态失败: 响应不是有效 JSON 对象")
        if info_data.get("mfa_enabled") is True or self._totp_factors(info_before):
            raise RuntimeError("ChatGPT 已启用 TOTP，但本地没有对应 2FA 密钥，无法恢复原密钥")

        result = self._enroll_totp(page, access_token)
        self._require_mfa_response(result, "2FA enroll")
        enroll = result.get("data") if isinstance(result, dict) else {}
        secret = str((enroll or {}).get("secret") or "").strip()
        session_id = str((enroll or {}).get("session_id") or "").strip()
        factor_id = str(((enroll or {}).get("factor") or {}).get("id") or "").strip()
        if not secret or not session_id:
            raise RuntimeError("2FA enroll 响应缺少 secret 或 session_id")

        activation: dict[str, Any] = {}
        for attempt in range(2):
            code = self._fresh_totp_code(secret, force_next_window=attempt > 0)
            activation = self._activate_totp(page, access_token, code, session_id)
            status = int((activation or {}).get("status") or 0)
            if status in {401, 403}:
                raise MFAReauthenticationRequired(f"2FA activate 要求重新认证: HTTP {status}")
            activation_data = activation.get("data") if isinstance(activation, dict) else {}
            if isinstance(activation, dict) and activation.get("ok") and isinstance(activation_data, dict) and activation_data.get("success") is True:
                break
        else:
            status = activation.get("status") if isinstance(activation, dict) else 0
            raise RuntimeError(f"2FA activate 失败: HTTP {status}")

        info_after = self._mfa_info(page, access_token)
        self._require_mfa_response(info_after, "确认 2FA 状态")
        info_after_data = info_after.get("data") or {}
        if not isinstance(info_after_data, dict):
            raise RuntimeError("确认 2FA 状态失败: 响应不是有效 JSON 对象")
        confirmed_factors = self._totp_factors(info_after)
        confirmed = bool(info_after_data.get("mfa_enabled") is True and confirmed_factors)
        if factor_id:
            confirmed = confirmed and any(str(item.get("id") or "") == factor_id for item in confirmed_factors)
        if not confirmed:
            raise RuntimeError("2FA activate 返回成功，但 mfa_info 未确认 TOTP 已启用")
        self.account.totp_secret = secret
        return secret, self._session_json(page)

    def _setup_2fa(self, page, password: str) -> tuple[str, dict[str, Any]]:
        page.goto(CHATGPT_BASE_URL, wait_until="domcontentloaded", timeout=60000)
        session_json = self._session_json(page)
        access_token = str(session_json.get("accessToken") or session_json.get("access_token") or "")
        try:
            return self._setup_2fa_protocol(page, access_token)
        except MFAReauthenticationRequired:
            self.log("[登录密钥] 2FA 协议接口要求重新认证，将使用已设置密码完成一次重认证后重试")
        session_json = self._reauth_for_2fa(page, password)
        access_token = str(session_json.get("accessToken") or session_json.get("access_token") or "")
        return self._setup_2fa_protocol(page, access_token)

    def run(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "password": self.account.chatgpt_password,
            "totp_secret": self.account.totp_secret,
            "password_added": False,
            "totp_added": False,
            "errors": [],
        }
        if result["password"] and result["totp_secret"]:
            result["skipped"] = True
            result["complete"] = True
            return result
        self._progress("login_secret_started")
        try:
            with open_registration_browser(
                headless=True,
                proxy_url=self.proxy_url,
                fingerprint=generate_register_fingerprint(),
                log=self.log,
                storage_state=self._storage_state(),
            ) as browser_session:
                context = browser_session.context
                if self.traffic_optimizer is not None:
                    self.traffic_optimizer.attach(context)
                page = context.new_page()
                page.goto(CHATGPT_BASE_URL, wait_until="domcontentloaded", timeout=60000)
                current_session = self._session_json(page)
                if not self.account.chatgpt_password:
                    self._progress("login_secret_password")
                    try:
                        password = self._add_password(page)
                        self.account.chatgpt_password = password
                        result.update({"password": password, "password_added": True})
                    except Exception as exc:
                        result["errors"].append(f"添加密码失败: {exc}")
                if not self.account.totp_secret and self.account.chatgpt_password:
                    self._progress("login_secret_2fa")
                    try:
                        secret, current_session = self._setup_2fa(page, self.account.chatgpt_password)
                        result.update({"totp_secret": secret, "totp_added": True})
                    except Exception as exc:
                        result["errors"].append(f"添加2FA失败: {exc}")
                result["session"] = {
                    **self.session,
                    "access_token": str(current_session.get("accessToken") or current_session.get("access_token") or self.session.get("access_token") or ""),
                    "session_json": current_session,
                    "storage_state_json": context.storage_state(),
                }
                result["complete"] = bool(result.get("password") and result.get("totp_secret"))
                self._progress("login_secret_completed" if result["complete"] else "login_secret_failed")
                return result
        finally:
            if self.reader:
                self.reader.close()


def setup_login_secret(
    account: MailAccount,
    session: dict[str, Any],
    proxy_url: str = "",
    log: Callable[[str], None] | None = None,
    *,
    should_cancel: Callable[[], bool] | None = None,
    mailbox_proxy_url: str | None = None,
    traffic_meter: ProxyTrafficMeter | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    return LoginSecretSetupFlow(
        account,
        session,
        proxy_url,
        log,
        should_cancel=should_cancel,
        mailbox_proxy_url=mailbox_proxy_url,
        traffic_meter=traffic_meter,
        on_progress=on_progress,
    ).run()
