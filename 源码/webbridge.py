"""Control the user's running Chrome through the Kimi browser extension.

The app owns a task tab when it has to create one; it never starts Chrome or
creates a Chrome profile.  All calls use one unique session per export run.
"""
import json
import re
import shutil
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import environment

URLS = {
    'jst': 'https://ss.erp321.com/profit-report/order-profit',
    'tmall': 'https://myseller.taobao.com/home.htm/whale-accountant/bill/summary?billDirection=income&billType=month',
}
TMALL_LOGIN_URL = 'https://login.taobao.com/member/login.jhtml'
COMMAND_URL = 'http://127.0.0.1:10086/command'
SHEET_SUFFIXES = {'.csv', '.xlsx', '.xls', '.xlsm'}
# 守护进程的鼠标/键盘是真实系统输入，只送达窗口前台标签页；发这些动作前必须先激活。
TRUSTED_INPUT_ACTIONS = frozenset({'mouse_click', 'key_type', 'send_keys'})


def _selector_js(label, row=None, scope_css=None):
    """Find one visible leaf by exact text, optionally inside one area or row."""
    return '''(()=>{const want=__LABEL__,rowKey=__ROW__,areaCSS=__SCOPE__,norm=t=>(t||'').replace(/[\\s\\u00a0]+/g,' ').trim();
      const wants=(Array.isArray(want)?want:[want]).map(t=>norm(t).toLowerCase());
      const matches=t=>wants.includes(norm(t).toLowerCase());
      let scope=document;
      if(areaCSS){const areas=[...document.querySelectorAll(areaCSS)].filter(e=>e.getClientRects().length);
        if(areas.length!==1)return 'SCOPES='+areas.length;scope=areas[0];}
      if(rowKey){const rows=[...scope.querySelectorAll('tr')].filter(e=>e.getClientRects().length&&norm(e.innerText).includes(rowKey));
        if(rows.length!==1)return 'ROWS='+rows.length;scope=rows[0];}
      const nodes=[...scope.querySelectorAll('button,[role=button],a,span,div,li,td')];
      const leaves=nodes.filter(e=>e.getClientRects().length&&matches(e.textContent))
        .filter(e=>!nodes.some(x=>x!==e&&e.contains(x)&&matches(x.textContent)));
      if(!leaves.length)return 'N=0';
      const dialogs=[...document.querySelectorAll('.next-overlay-inner,[role=dialog],.next-dialog')]
        .filter(e=>e.getClientRects().length);
      const inDialog=leaves.filter(e=>dialogs.some(d=>d.contains(e)));
      const clickable=leaves.filter(e=>e.closest('button,[role=button],a'));
      const hits=inDialog.length?inDialog:(clickable.length?clickable:leaves);
      if(hits.length!==1)return 'N='+hits.length;
      let e=hits[0],parts=[];
      while(e&&e.nodeType===1&&parts.length<12){let n=1,p=e;
        while((p=p.previousElementSibling))if(p.tagName===e.tagName)n++;
        parts.unshift(e.tagName.toLowerCase()+':nth-of-type('+n+')');e=e.parentElement;}
      return parts.join('>');})()'''.replace('__LABEL__', json.dumps(label, ensure_ascii=False)).replace('__ROW__', json.dumps(row, ensure_ascii=False)).replace('__SCOPE__', json.dumps(scope_css, ensure_ascii=False))


def _hit_test_js(selector):
    return '''(()=>{const e=document.querySelector(SEL);if(!e)return '元素已消失';
      e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect();
      if(r.width<1||r.height<1)return '元素不可见';
      const x=Math.round(r.left+r.width/2),y=Math.round(r.top+r.height/2),top=document.elementFromPoint(x,y);
      if(top===e||e.contains(top)||top&&top.contains(e))return 'OK';
      const cls=top&&typeof top.className==='string'&&top.className.trim()
        ?'.'+top.className.trim().replace(/\\s+/g,'.').slice(0,60):'';
      return '被 '+(top?.tagName||'未知元素')+cls+' 挡住';})()'''.replace('SEL', json.dumps(selector))


class Chrome:
    """Small compatibility surface used by Engine and the Jushuitan adapter."""
    def __init__(self, folder, account, executable=''):
        self.account = account
        self.session = 'commerce-bill-' + uuid.uuid4().hex
        self.opened = False
        self.owned_tab = False
        self.last_download_name = ''
        self.last_detail_claim = None

    def activate(self):
        """把当前标签页带到所在窗口的前台。

        真实输入（mouse_click / key_type / send_keys）只送达可接收输入的标签页。
        先激活再发送；仅点击确认完全未送达时才允许再试一次，键盘输入不能
        盲目重试，因为部分字符可能已经输入。
        """
        try:
            self.call('cdp', {'method': 'Page.bringToFront', 'params': {}})
        except RuntimeError:
            pass
        time.sleep(.2)

    def _send(self, action, args, timeout):
        body = json.dumps({'action': action, 'args': args or {}, 'session': self.session},
                          ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(COMMAND_URL, data=body,
                                         headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise RuntimeError(f'浏览器运行端没有响应（{exc}）。请在设置中重新检查浏览器运行环境') from None

    def call(self, action, args=None, timeout=120):
        if action in TRUSTED_INPUT_ACTIONS:
            self.activate()
        payload = self._send(action, args, timeout)
        if not payload.get('ok'):
            error = payload.get('error') or {}
            raise RuntimeError(f'{action} 失败：{error.get("message", error)}')
        return payload.get('data') or {}

    def rpc(self, action, args=None):
        if action == 'find_tab' and self.opened:
            try:
                url = self.js('location.href') or ''
                if url.split('?')[0] == (args or {}).get('url', '').split('?')[0]:
                    return {'success': True, 'url': url}
            except RuntimeError:
                pass
        if action == 'navigate' and self.opened:
            self.open_url((args or {})['url'])
            return {'success': True}
        return self.call(action, args)

    def js(self, code):
        return self.call('evaluate', {'code': code}).get('value')

    def evaluate(self, code, main_world=False):
        return self.call('evaluate', {'code': code})

    def start(self, url=None):
        state = environment.probe()
        if environment.verdict(state) != 'ready':
            raise RuntimeError(environment.describe(state)['title'] + '。请先在 APP 顶部完成设置')
        self.open_url(url or URLS[self.account['platform']])

    def open_url(self, url, timeout=45):
        if not self.opened:
            try:
                self.call('find_tab', {'url': url, 'active': True}, timeout=timeout)
            except RuntimeError:
                self.call('navigate', {'url': url, 'newTab': True,
                                       'group_title': '电商账单导出'}, timeout=timeout)
                self.owned_tab = True
            else:
                self.call('navigate', {'url': url, 'newTab': False}, timeout=timeout)
            self.opened = True
        else:
            self.call('navigate', {'url': url, 'newTab': False}, timeout=timeout)
        self.wait(lambda: self.js('document.readyState') in ('interactive', 'complete'), timeout)
        # find_tab 可能找到的是窗口里的后台标签；导航后统一带到前台，后续真实输入才有保证。
        self.activate()

    def wait(self, predicate, timeout=20, interval=.5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                value = predicate()
                if value:
                    return value
            except RuntimeError:
                pass
            time.sleep(interval)
        return None

    def selector(self, label, row=None, scope_css=None):
        return self.js(_selector_js(label, row, scope_css))

    def click_text(self, label, row=None, timeout=20, scope_css=None):
        display = '／'.join(label) if isinstance(label, (tuple, list)) else label
        selector = self.wait(lambda: self._found_selector(label, row, scope_css), timeout)
        if not selector:
            state = self.selector(label, row, scope_css)
            where = f'（行：{row}）' if row else f'（范围：{scope_css}）' if scope_css else ''
            raise RuntimeError(f'页面上找不到唯一的「{display}」{where}；定位结果：{state or "无"}；'
                               f'当前页面：{self.js("location.pathname") or "未知"}')
        deadline = time.monotonic() + 20
        while True:
            try:
                return self.click(selector, display)
            except RuntimeError as exc:
                # 淘宝表格随时重渲染 DOM：定位、命中测试、守护进程按路径重找，
                # 三步之间按钮节点可能被替换（报 element not found / 元素已消失）。
                # 两者都发生在真实点击派发之前，点击确定没有发生，重新定位重试安全。
                message = str(exc)
                if ('element not found' not in message and '元素已消失' not in message):
                    raise
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.8)
                # 旧 nth-of-type 路径在表格重排后可能指向别的按钮；必须拿到
                # 唯一的新路径，找不到就停止，不能沿用旧路径继续点击。
                selector = self.wait(lambda: self._found_selector(label, row, scope_css), 8)
                if not selector:
                    raise RuntimeError(f'页面更新后找不到唯一的「{display}」'
                                       + (f'（行：{row}）' if row else '')) from exc

    def _found_selector(self, label, row, scope_css=None):
        value = self.selector(label, row, scope_css)
        return value if value and not value.startswith(('N=', 'ROWS=', 'SCOPES=')) else None

    def key_escape(self):
        """发一次真实 Esc，收掉页面上的气泡、下拉、日历、对话框类浮层。"""
        self.activate()
        for type_ in ('rawKeyDown', 'keyUp'):
            try:
                self.call('cdp', {'method': 'Input.dispatchKeyEvent', 'params': {
                    'type': type_, 'key': 'Escape', 'code': 'Escape',
                    'windowsVirtualKeyCode': 27, 'nativeVirtualKeyCode': 27}})
            except RuntimeError:
                pass
        time.sleep(.2)

    def click(self, selector, label='按钮'):
        state = self.js(_hit_test_js(selector))
        deadline = time.monotonic() + 10
        dismissed_popup = False
        while state != 'OK' and time.monotonic() < deadline:
            # Loading masks normally leave on their own.  Escape is reserved
            # for clearly identified calendar/popover UI: repeatedly pressing
            # it on an arbitrary DIV could close the download dialog itself.
            if (not dismissed_popup and state.startswith('被 ') and
                    any(name in state.lower() for name in
                        ('calendar', 'popover', 'tooltip', 'balloon'))):
                self.key_escape()
                dismissed_popup = True
            else:
                time.sleep(.3)
            state = self.js(_hit_test_js(selector))
        if state != 'OK':
            if state.startswith('被 '):
                raise RuntimeError(f'「{label}」点不到：{state}；等待 10 秒后仍被页面元素遮挡。'
                                   '请在 Chrome 里查看该浮层')
            raise RuntimeError(f'「{label}」点不到：{state}；等待后仍无法定位按钮')
        # Alibaba pages ignore DOM synthetic clicks for downloads.  The local
        # bridge's mouse_click path has been verified against both monthly pages.
        self._mouse_click(selector)

    def _mouse_click(self, selector, manage_focus=True):
        """Deliver trusted input to this tab, including when another tab is shown."""
        if manage_focus:
            self.call('cdp', {'method': 'Emulation.setFocusEmulationEnabled',
                              'params': {'enabled': True}})
        try:
            try:
                return self.call('mouse_click', {'selector': selector})
            except RuntimeError as exc:
                # This specific bridge error confirms no pointerdown or mousedown
                # reached the page (daemon wording: "no pointerdown/mousedown
                # fired").  A retry is safe; other failures may have clicked the
                # button already, so must never be retried.
                message = str(exc)
                if ('click did not reach the page' not in message or
                        'no pointerdown/mouse' not in message):
                    raise
                self.call('cdp', {'method': 'Page.bringToFront', 'params': {}})
                state = self.js(_hit_test_js(selector))
                if state != 'OK':
                    raise RuntimeError(f'浏览器标签已切到前台，但按钮仍点不到：{state}') from exc
                return self.call('mouse_click', {'selector': selector})
        finally:
            if manage_focus:
                try:
                    self.call('cdp', {'method': 'Emulation.setFocusEmulationEnabled',
                                      'params': {'enabled': False}})
                except RuntimeError:
                    pass

    def fill(self, selector, value):
        self.call('fill', {'selector': selector, 'value': value})
        return self.js(f'(()=>{{const e=document.querySelector({json.dumps(selector)});return e?e.value:null}})()')

    def wait_for_login_form(self, timeout=12):
        return bool(self.wait(lambda: (self._login_fields() or {}).get('found'), timeout))

    def _login_fields(self):
        """Read one unambiguous form in the current document, without credentials."""
        return self.js(r'''(()=>{const visible=e=>e.getClientRects().length&&!e.disabled;
          const inputs=[...document.querySelectorAll('input')].filter(visible);
          const passwords=inputs.filter(e=>e.type==='password');
          const candidates=inputs.filter(e=>!['password','checkbox','radio','hidden','submit','button'].includes(e.type));
          const named=candidates.filter(e=>/user|account|loginid|mobile|phone|账号|手机号|邮箱/i.test(
            [e.name,e.id,e.placeholder,e.autocomplete].join(' ')));
          const users=named.length?named:candidates;
          const buttons=[...document.querySelectorAll('button,input[type=submit],[role=button]')]
            .filter(visible).filter(e=>/^(登录|登\s*录|立即登录|Login|Sign in)$/i.test(
              (e.textContent||e.value||'').trim()));
          const path=e=>{let parts=[];while(e&&e.nodeType===1&&parts.length<12){let n=1,s=e;
            while((s=s.previousElementSibling))if(s.tagName===e.tagName)n++;
            parts.unshift(e.tagName.toLowerCase()+':nth-of-type('+n+')');e=e.parentElement;}
            return parts.join('>')};
          const consentLabel=e=>e.closest('label')||
            (e.id?[...document.querySelectorAll('label[for]')].find(l=>l.htmlFor===e.id):null);
          const consents=[...document.querySelectorAll('input[type=checkbox]')].filter(e=>{
            const label=consentLabel(e);
            const text=[e.id,e.name,e.className,label?.textContent,e.parentElement?.textContent].join(' ');
            return !e.checked&&/同意|协议|agree|protocol|agreement/i.test(text)&&
              (visible(e)||label&&visible(label));
          }).map(e=>({box:path(e),click:path(consentLabel(e)||e)}));
          return {found:passwords.length===1&&users.length===1,
            user:users.length===1?path(users[0]):'',password:passwords.length===1?path(passwords[0]):'',
            button:buttons.length===1?path(buttons[0]):'',consents};})()''') or {}

    def _type_login_field(self, selector, value):
        # Focus emulation lets trusted keyboard input reach this Chrome tab
        # without launching a second browser or replacing the user's profile.
        self._mouse_click(selector, manage_focus=False)
        self.js('''(()=>{const e=document.querySelector(__SEL__);if(!e)return false;
          e.focus();e.select();return true})()'''.replace('__SEL__', json.dumps(selector)))
        self.call('key_type', {'text': value, 'delay': 65})
        return self.js('''(()=>{const e=document.querySelector(__SEL__);
          return !!e&&e.value===__VALUE__})()'''.replace('__SEL__', json.dumps(selector))
                    .replace('__VALUE__', json.dumps(value, ensure_ascii=False))) is True

    def autofill(self, username, password):
        """Type saved credentials, accept required login terms and submit."""
        if not username or not password:
            return {'filled': False, 'submitted': False, 'reason': 'missing_credentials'}
        trusted_hosts = {'tmall': 'taobao.com', 'jst': 'erp321.com'}
        suffix = trusted_hosts.get(self.account.get('platform'))
        if suffix:
            host = str(self.js('location.hostname') or '').lower()
            if host != suffix and not host.endswith('.' + suffix):
                return {'filled': False, 'submitted': False, 'reason': 'unsafe_host'}
        fields = self._login_fields()
        if not fields.get('found'):
            return {'filled': False, 'submitted': False, 'reason': 'form_missing'}
        self.call('cdp', {'method': 'Emulation.setFocusEmulationEnabled',
                          'params': {'enabled': True}})
        try:
            if not self._type_login_field(fields['user'], username):
                return {'filled': False, 'submitted': False, 'reason': 'user_rejected'}
            if not self._type_login_field(fields['password'], password):
                return {'filled': False, 'submitted': False, 'reason': 'password_rejected'}
            # Login agreements are part of the requested automatic sign-in.
            # Click the visible label with trusted input, then verify the actual
            # checkbox state before submitting.  An unchecked box must never
            # result in a misleading "submitted" response.
            for consent in fields['consents']:
                self._mouse_click(consent['click'], manage_focus=False)
                checked = self.wait(lambda: self.js('''(()=>{const e=document.querySelector(__BOX__);
                  return !!e&&e.checked})()'''.replace('__BOX__', json.dumps(consent['box']))),
                                    timeout=2, interval=.1)
                if not checked:
                    return {'filled': True, 'submitted': False, 'reason': 'consent_failed'}
            fields = self._login_fields()
            if not fields.get('found'):
                return {'filled': True, 'submitted': False, 'reason': 'form_changed'}
            if fields.get('consents'):
                return {'filled': True, 'submitted': False, 'reason': 'consent_failed'}
            # Chrome's own autofill may overwrite a field after the second click.
            if not self.js('''(()=>{const u=document.querySelector(__USER__),p=document.querySelector(__PASS__);
              return !!u&&!!p&&u.value===__USERNAME__&&p.value===__PASSWORD__})()'''
                           .replace('__USER__', json.dumps(fields['user']))
                           .replace('__PASS__', json.dumps(fields['password']))
                           .replace('__USERNAME__', json.dumps(username, ensure_ascii=False))
                           .replace('__PASSWORD__', json.dumps(password, ensure_ascii=False))):
                return {'filled': False, 'submitted': False, 'reason': 'overwritten'}
            if not fields['button']:
                return {'filled': True, 'submitted': False, 'reason': 'button_missing'}
            self._mouse_click(fields['button'], manage_focus=False)
            return {'filled': True, 'submitted': True, 'reason': ''}
        except RuntimeError:
            # Keep the password out of diagnostics and let the UI offer a
            # manual fallback when a field or trusted input was rejected.
            return {'filled': False, 'submitted': False, 'reason': 'input_failed'}
        finally:
            try:
                self.call('cdp', {'method': 'Emulation.setFocusEmulationEnabled',
                                  'params': {'enabled': False}})
            except RuntimeError:
                pass

    def is_logged_out(self):
        value = self.js("(()=>/login|signin/i.test(location.hostname+location.pathname)||Array.from(document.querySelectorAll('input[type=password]')).some(e=>e.getClientRects().length))()")
        return bool(value)

    def logout(self):
        # The account-menu UI differs between the two sites.  Only claim success
        # after the page reports an actual logged-out state.
        for label in ('退出当前账号', '退出登录', '退出账号', '退出'):
            selector = self._found_selector(label, None)
            if selector:
                try:
                    self.click(selector, label)
                    if self.wait(self.is_logged_out, 8):
                        return True
                except RuntimeError:
                    pass
        return self.is_logged_out()

    def current_shop(self):
        return self.js(r'''(()=>{const t=(document.body.innerText||'').replace(/[\s\u00a0]+/g,' ');
          const m=t.match(/客服\s+([^\s]{2,30}?(?:旗舰店|专营店|专卖店|企业店|官方店|店))\s/);
          return m?m[1]:'';})()''') or ''

    def download(self, trigger, folder, name, timeout=180):
        """Observe Chrome's Downloads folder; accept exactly one monthly file."""
        downloads = Path.home() / 'Downloads'
        if not downloads.is_dir():
            raise RuntimeError(f'找不到 Chrome 默认下载目录：{downloads}；请检查浏览器下载位置')
        before = {p.name: (p.stat().st_mtime_ns, p.stat().st_size)
                  for p in downloads.iterdir() if p.is_file()}
        trigger()
        month = re.search(r'(20\d{2})-(0[1-9]|1[0-2])', name)
        expected = ''.join(month.groups()) if month else ''
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            matches = []
            for path in downloads.iterdir():
                if not path.is_file() or path.suffix.lower() not in SHEET_SUFFIXES:
                    continue
                stamp = (path.stat().st_mtime_ns, path.stat().st_size)
                if before.get(path.name) == stamp or not stamp[1]:
                    continue
                if expected and expected not in path.name:
                    continue
                if (downloads / (path.name + '.crdownload')).exists():
                    continue
                matches.append((path, stamp))
            if len(matches) > 1:
                raise RuntimeError('下载目录里出现多份同月份新文件，无法判断哪份来自本次导出；请检查 Chrome 下载记录')
            if matches:
                path, stamp = matches[0]
                time.sleep(.8)
                if path.exists() and (path.stat().st_mtime_ns, path.stat().st_size) == stamp:
                    target = Path(folder) / (name + path.suffix.lower())
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
                    self.last_download_name = path.name
                    return target
            time.sleep(.5)
        raise RuntimeError('Chrome 没有在默认下载目录保存该月份明细；请检查浏览器下载位置、页面提示及验证码')

    def close(self):
        if self.opened and self.owned_tab:
            try:
                self.call('close_tab', {})
            except RuntimeError:
                pass
        self.opened = False
