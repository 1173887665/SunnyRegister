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
    ):
        self.account = account
        self.session = dict(session or {})
        self.proxy_url = str(proxy_url or "")
        self.mailbox_proxy_url = self.proxy_url if mailbox_proxy_url is None else str(mailbox_proxy_url or "")
        self.log = log or (lambda _message: None)
        self.should_cancel = should_cancel or (lambda: False)
        self.traffic_meter = traffic_meter
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
    def _click_password_action(page) -> bool:
        return bool(page.evaluate(
            r"""() => {
                const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
                    && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
                    && !el.disabled && String(el.getAttribute('aria-disabled') || '').toLowerCase() !== 'true';
                const desc = el => [el.innerText, el.textContent, el.getAttribute('aria-label'), el.title, el.getAttribute('data-testid')]
                    .filter(Boolean).join(' ').replace(/\s+/g, ' ').trim().toLowerCase();
                const items = [...document.querySelectorAll('button,a,[role="button"],[role="link"]')].filter(visible);
                const hit = items.find(el => /password|密码|パスワード|비밀번호/.test(desc(el))
                    && /add|create|set|update|change|manage|添加|创建|设置|更新|更改|管理|追加|変更|설정|변경/.test(desc(el)));
                if (!hit) return false;
                hit.scrollIntoView({block:'center'}); hit.click(); return true;
            }"""
        ))

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
        page.goto(f"{CHATGPT_BASE_URL}/#settings/Account", wait_until="domcontentloaded", timeout=60000)
        deadline = time.time() + 45
        while time.time() < deadline:
            self._check_cancelled()
            if self._click_password_action(page):
                break
            self._sleep(1)
        else:
            raise RuntimeError("账户设置中未找到添加密码入口")
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

    def _setup_2fa(self, page, password: str) -> tuple[str, dict[str, Any]]:
        session_json = self._reauth_for_2fa(page, password)
        access_token = str(session_json.get("accessToken") or session_json.get("access_token") or "")
        result = page.evaluate(
            """async token => {
                const headers = {'accept':'application/json','content-type':'application/json','authorization':'Bearer '+token};
                const enroll = await fetch('/backend-api/accounts/mfa/enroll', {method:'POST',credentials:'include',headers,body:JSON.stringify({factor_type:'totp'})});
                const enrollText = await enroll.text(); let enrollData={}; try { enrollData=JSON.parse(enrollText); } catch (_) {}
                return {ok:enroll.ok,status:enroll.status,data:enrollData,text:enrollText};
            }""",
            access_token,
        )
        enroll = result.get("data") if isinstance(result, dict) else {}
        secret = str((enroll or {}).get("secret") or "").strip()
        session_id = str((enroll or {}).get("session_id") or "").strip()
        if not result.get("ok") or not secret or not session_id:
            raise RuntimeError(f"2FA enroll 失败: HTTP {result.get('status')}")
        activation = page.evaluate(
            """async ({token,code,sessionId}) => {
                const response = await fetch('/backend-api/accounts/mfa/user/activate_enrollment', {
                    method:'POST',credentials:'include',
                    headers:{'accept':'application/json','content-type':'application/json','authorization':'Bearer '+token},
                    body:JSON.stringify({code,factor_type:'totp',session_id:sessionId})
                });
                const text=await response.text(); let data={}; try { data=JSON.parse(text); } catch (_) {}
                return {ok:response.ok,status:response.status,data,text};
            }""",
            {"token": access_token, "code": generate_totp(secret), "sessionId": session_id},
        )
        if not activation.get("ok") or (activation.get("data") or {}).get("success") is not True:
            raise RuntimeError(f"2FA activate 失败: HTTP {activation.get('status')}")
        self.account.totp_secret = secret
        return secret, self._session_json(page)

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
                    try:
                        password = self._add_password(page)
                        self.account.chatgpt_password = password
                        result.update({"password": password, "password_added": True})
                    except Exception as exc:
                        result["errors"].append(f"添加密码失败: {exc}")
                if not self.account.totp_secret and self.account.chatgpt_password:
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
) -> dict[str, Any]:
    return LoginSecretSetupFlow(
        account,
        session,
        proxy_url,
        log,
        should_cancel=should_cancel,
        mailbox_proxy_url=mailbox_proxy_url,
        traffic_meter=traffic_meter,
    ).run()
