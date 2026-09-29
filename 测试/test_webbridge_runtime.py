"""Offline checks for the new production browser path."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import engine
import environment
import tmall_bridge
import webbridge


class BrowserDownloadTests(unittest.TestCase):
    def test_login_input_waits_for_controlled_field_to_settle(self):
        browser = webbridge.Chrome(Path('/tmp'), {'platform': 'tmall'})
        with patch.object(browser, '_mouse_click'), \
                patch.object(browser, 'call') as call, \
                patch.object(browser, 'js', side_effect=[True, False, True]):
            self.assertTrue(browser._type_login_field('#password', 'example-password'))
        call.assert_called_once_with('key_type', {'text': 'example-password', 'delay': 65})

    def test_rerendered_row_reselects_button_before_retry(self):
        browser = webbridge.Chrome(Path('/tmp'), {'platform': 'tmall'})
        with patch.object(browser, 'wait', side_effect=['#old', '#new']), \
                patch.object(browser, 'click', side_effect=[RuntimeError('element not found'), None]) as click, \
                patch.object(webbridge.time, 'sleep'):
            browser.click_text('下载明细', row='202608')
        self.assertEqual([c.args[0] for c in click.call_args_list], ['#old', '#new'])

    def test_rerendered_row_never_reuses_stale_selector(self):
        browser = webbridge.Chrome(Path('/tmp'), {'platform': 'tmall'})
        with patch.object(browser, 'wait', side_effect=['#old', None]), \
                patch.object(browser, 'click', side_effect=RuntimeError('element not found')) as click, \
                patch.object(webbridge.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, '页面更新后找不到唯一'):
                browser.click_text('下载明细', row='202608')
        click.assert_called_once_with('#old', '下载明细')

    def test_loading_mask_clears_without_closing_dialog(self):
        browser = webbridge.Chrome(Path('/tmp'), {'platform': 'tmall'})
        with patch.object(browser, 'js', side_effect=['被 DIV.next-loading 挡住', 'OK']), \
                patch.object(browser, 'key_escape') as escape, \
                patch.object(browser, '_mouse_click') as click, \
                patch.object(webbridge.time, 'sleep'):
            browser.click('#detail', '下载明细')
        escape.assert_not_called()
        click.assert_called_once_with('#detail')

    def test_calendar_popup_gets_only_one_escape(self):
        browser = webbridge.Chrome(Path('/tmp'), {'platform': 'tmall'})
        with patch.object(browser, 'js', side_effect=['被 DIV.next-calendar2-panel 挡住',
                                                     '被 DIV.next-calendar2-panel 挡住', 'OK']), \
                patch.object(browser, 'key_escape') as escape, \
                patch.object(browser, '_mouse_click') as click, \
                patch.object(webbridge.time, 'sleep'):
            browser.click('#detail', '下载明细')
        escape.assert_called_once()
        click.assert_called_once_with('#detail')

    def test_trusted_input_activates_tab_but_never_retries_keyboard(self):
        browser = webbridge.Chrome(Path('/tmp'), {'platform': 'tmall'})
        with patch.object(browser, 'activate') as activate, patch.object(browser, '_send') as send:
            send.return_value = {'ok': False, 'error': {'message': 'input uncertain'}}
            with self.assertRaisesRegex(RuntimeError, 'input uncertain'):
                browser.call('key_type', {'text': 'abc'})
        activate.assert_called_once()
        send.assert_called_once()

    def test_trusted_click_enables_and_restores_tab_focus(self):
        browser = webbridge.Chrome(Path('/tmp'), {'platform': 'tmall'})
        with patch.object(browser, 'js', return_value='OK'), patch.object(browser, 'call') as call:
            browser.click('#search', '查询')
        self.assertEqual([(c.args[0], c.args[1]) for c in call.call_args_list], [
            ('cdp', {'method': 'Emulation.setFocusEmulationEnabled', 'params': {'enabled': True}}),
            ('mouse_click', {'selector': '#search'}),
            ('cdp', {'method': 'Emulation.setFocusEmulationEnabled', 'params': {'enabled': False}}),
        ])

    def test_background_tab_retries_only_after_confirmed_no_click(self):
        for event in ('mouseup', 'mousedown'):
            with self.subTest(event=event):
                browser = webbridge.Chrome(Path('/tmp'), {'platform': 'tmall'})
                actions = []

                def call(action, args):
                    actions.append((action, args))
                    if action == 'mouse_click' and sum(a == 'mouse_click' for a, _ in actions) == 1:
                        raise RuntimeError('mouse_click 失败: click did not reach the page — '
                                           f'no pointerdown/{event} fired')
                    return {}

                with patch.object(browser, 'js', return_value='OK'), \
                        patch.object(browser, 'call', side_effect=call):
                    browser.click('#download', '下载')
                self.assertEqual([a for a, _ in actions],
                                 ['cdp', 'mouse_click', 'cdp', 'mouse_click', 'cdp'])
                self.assertEqual(actions[2][1]['method'], 'Page.bringToFront')

    def test_ambiguous_click_error_is_never_retried(self):
        browser = webbridge.Chrome(Path('/tmp'), {'platform': 'tmall'})
        actions = []

        def call(action, args):
            actions.append((action, args))
            if action == 'mouse_click':
                raise RuntimeError('response lost after dispatch')
            return {}

        with patch.object(browser, 'js', return_value='OK'), patch.object(browser, 'call', side_effect=call):
            with self.assertRaisesRegex(RuntimeError, 'response lost'):
                browser.click('#download', '下载')
        self.assertEqual([a for a, _ in actions], ['cdp', 'mouse_click', 'cdp'])
        self.assertEqual(actions[-1][1]['params'], {'enabled': False})

    def test_saved_password_is_not_typed_on_untrusted_login_host(self):
        browser = webbridge.Chrome(Path('/tmp'), {'platform': 'tmall'})
        with patch.object(browser, 'js', return_value='unexpected.example'), \
                patch.object(browser, 'call') as call:
            result = browser.autofill('saved-user', 'saved-password')
        self.assertEqual(result['reason'], 'unsafe_host')
        call.assert_not_called()

    def test_only_the_new_matching_month_is_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            downloads = root / 'Downloads'
            downloads.mkdir()
            (downloads / '交易货款_202607_202607.csv').write_text('older', encoding='utf8')
            browser = webbridge.Chrome(root, {'platform': 'tmall'})

            def trigger():
                (downloads / '交易货款_202608_202608.csv').write_text('monthly data', encoding='utf8')

            with patch.object(webbridge.Path, 'home', return_value=root):
                result = browser.download(trigger, root / 'task', 'bill_income-2026-08', timeout=2)
            self.assertEqual(result.read_text('utf8'), 'monthly data')
            self.assertEqual(browser.last_download_name, '交易货款_202608_202608.csv')

    def test_download_timeout_does_not_reuse_old_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            downloads = root / 'Downloads'
            downloads.mkdir()
            (downloads / '交易货款_202608_202608.csv').write_text('old', encoding='utf8')
            browser = webbridge.Chrome(root, {'platform': 'tmall'})
            with patch.object(webbridge.Path, 'home', return_value=root):
                with self.assertRaisesRegex(RuntimeError, '没有在默认下载目录'):
                    browser.download(lambda: None, root / 'task', 'bill_income-2026-08', timeout=.1)


class MonthAndAccountGuards(unittest.TestCase):
    def test_range_results_must_survive_query_refresh(self):
        browser = Mock()
        browser.wait.side_effect = [True, None]
        with patch.object(tmall_bridge.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, '出现后又消失'):
                tmall_bridge._wait_any_target_row(browser, ['2026-08'])
        self.assertEqual(browser.wait.call_count, 2)

    def test_range_results_are_accepted_after_second_check(self):
        browser = Mock()
        browser.wait.side_effect = [True, True]
        with patch.object(tmall_bridge.time, 'sleep'):
            tmall_bridge._wait_any_target_row(browser, ['2026-08'])
        self.assertEqual(browser.wait.call_count, 2)

    def test_bill_month_waits_and_recovers_month_tab(self):
        browser = Mock()
        browser.wait.side_effect = [None, True, True, True]
        browser.js.side_effect = lambda code: (
            ['2026-08', '2026-08'] if 'map(s=>document.querySelector' in code
            else '2026-08' if 'e?e.value' in code
            else True if '!!document.querySelector' in code or 'e.focus' in code
            else '')
        tmall_bridge._set_bill_month(browser, '2026-08')
        self.assertEqual([c.args[0] for c in browser.click_text.call_args_list],
                         ['收入账单', '月汇总'])
        # 月份改用逐字输入（fill 不被 Fusion 月份选择器接受）；两个输入框各敲一次。
        key_types = [c for c in browser.call.call_args_list if c.args[0] == 'key_type']
        self.assertEqual(len(key_types), 2)
        self.assertEqual([c.args[1]['keys'] for c in browser.call.call_args_list
                          if c.args[0] == 'send_keys'].count('Enter'), 2)
        browser.fill.assert_not_called()

    def test_bill_month_never_types_into_a_page_without_controls(self):
        browser = Mock()
        browser.wait.return_value = None
        browser.js.side_effect = lambda code: ([] if '.map(e=>e.placeholder)' in code
                                               else 'myseller.taobao.com/home.htm')
        with self.assertRaisesRegex(RuntimeError, '没有加载出开始/结束月份输入框'):
            tmall_bridge._set_bill_month(browser, '2026-08')
        browser.fill.assert_not_called()

    def test_windows_x64_process_on_arm_host_gets_x64_bridge(self):
        with patch.object(environment.sys, 'platform', 'win32'), \
                patch.object(environment.sysconfig, 'get_platform', return_value='win-amd64'), \
                patch.object(environment.platform, 'machine', return_value='ARM64'):
            self.assertEqual(environment.platform_key(), 'windows-amd64')

    def test_bill_stops_before_download_when_result_has_wrong_month(self):
        browser = Mock()
        browser.js.side_effect = lambda code: (
            ['2026-08', '2026-08'] if 'map(s=>document.querySelector' in code
            else '2026-08' if 'e?e.value' in code
            else True if '!!document.querySelector' in code or 'e.focus' in code
            else '')
        browser.wait.side_effect = [True, True, True, None]
        with patch.object(tmall_bridge.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, '范围查询没有返回任何目标月份行'):
                tmall_bridge.fetch_month(browser, 'bill_income', '2026-08', Path('/tmp'))
        browser.download.assert_not_called()

    def test_account_mismatch_stops_before_export(self):
        store = Mock(items=[], password=Mock(return_value=''))
        account = {'id': 'account-1', 'name': '目标旗舰店', 'platform': 'tmall', 'username': ''}
        with tempfile.TemporaryDirectory() as tmp, patch.object(engine, 'Chrome') as browser_type:
            browser = browser_type.return_value
            browser.wait.return_value = '另一旗舰店'
            worker = engine.Engine(tmp, store, lambda *args: None, lambda *args, **kwargs: None)
            with self.assertRaisesRegex(RuntimeError, '店铺未核对通过'):
                worker.open_account(account)
            browser.open_url.assert_called_once()

    def test_saved_tmall_credentials_login_without_manual_prompt(self):
        store = Mock(items=[], password=Mock(return_value='stored-password'))
        account = {'id': 'account-1', 'name': '目标旗舰店', 'platform': 'tmall',
                   'username': '目标旗舰店:员工'}
        ask = Mock()
        with tempfile.TemporaryDirectory() as tmp, patch.object(engine, 'Chrome') as browser_type:
            browser = browser_type.return_value
            browser.wait.side_effect = ['', True, '目标旗舰店']
            browser.js.return_value = 'https://www.taobao.com/'
            browser.wait_for_login_form.return_value = True
            browser.autofill.return_value = {'filled': True, 'submitted': True}
            worker = engine.Engine(tmp, store, lambda *args: None, ask)
            result, _, _ = worker.open_account(account)
            self.assertIs(result, browser)
            browser.open_url.assert_any_call(engine.TMALL_LOGIN_URL)
            browser.autofill.assert_called_once_with('目标旗舰店:员工', 'stored-password')
            ask.assert_not_called()

    def test_unchecked_terms_are_submitted_without_manual_prompt(self):
        store = Mock(items=[], password=Mock(return_value='stored-password'))
        account = {'id': 'account-1', 'name': '目标旗舰店', 'platform': 'tmall',
                   'username': '目标旗舰店:员工'}
        ask = Mock()
        with tempfile.TemporaryDirectory() as tmp, patch.object(engine, 'Chrome') as browser_type:
            browser = browser_type.return_value
            browser.wait.side_effect = ['', True, '目标旗舰店']
            browser.wait_for_login_form.return_value = True
            browser.autofill.return_value = {'filled': True, 'submitted': True, 'reason': ''}
            worker = engine.Engine(tmp, store, lambda *args: None, ask)
            worker.open_account(account)
            ask.assert_not_called()

    def test_consent_failure_stops_without_manual_login_dialog(self):
        store = Mock(items=[], password=Mock(return_value='stored-password'))
        account = {'id': 'account-1', 'name': '目标旗舰店', 'platform': 'tmall',
                   'username': '目标旗舰店:员工'}
        ask = Mock()
        with tempfile.TemporaryDirectory() as tmp, patch.object(engine, 'Chrome') as browser_type:
            browser = browser_type.return_value
            browser.wait.return_value = ''
            browser.wait_for_login_form.return_value = True
            browser.autofill.return_value = {'filled': True, 'submitted': False,
                                             'reason': 'consent_failed'}
            worker = engine.Engine(tmp, store, lambda *args: None, ask)
            with self.assertRaisesRegex(RuntimeError, '协议勾选未成功'):
                worker.open_account(account)
            ask.assert_not_called()

    def test_wrong_open_shop_is_logged_out_before_autologin(self):
        store = Mock(items=[], password=Mock(return_value='stored-password'))
        account = {'id': 'account-1', 'name': '目标旗舰店', 'platform': 'tmall',
                   'username': '目标旗舰店:员工'}
        with tempfile.TemporaryDirectory() as tmp, patch.object(engine, 'Chrome') as browser_type, \
                patch.object(engine.tmall, 'logout') as logout:
            browser = browser_type.return_value
            browser.wait.side_effect = ['其他旗舰店', True, '目标旗舰店']
            browser.js.return_value = 'https://www.taobao.com/'
            browser.wait_for_login_form.return_value = True
            browser.autofill.return_value = {'filled': True, 'submitted': True}
            worker = engine.Engine(tmp, store, lambda *args: None, Mock())
            worker.open_account(account)
            logout.assert_called_once_with(browser)

    def test_verification_url_is_not_claimed_as_logged_in(self):
        store = Mock(items=[], password=Mock(return_value='stored-password'))
        account = {'id': 'account-1', 'name': '目标旗舰店', 'platform': 'tmall',
                   'username': '目标旗舰店:员工'}
        ask = Mock()
        with tempfile.TemporaryDirectory() as tmp, patch.object(engine, 'Chrome') as browser_type:
            browser = browser_type.return_value
            shops = iter(['', '目标旗舰店'])
            browser.wait.side_effect = lambda predicate, timeout: (
                predicate() if timeout == 45 else next(shops))
            browser.js.return_value = 'https://verify.taobao.com/captcha'
            browser.wait_for_login_form.return_value = True
            browser.autofill.return_value = {'filled': True, 'submitted': True}
            worker = engine.Engine(tmp, store, lambda *args: None, ask)
            with self.assertRaisesRegex(RuntimeError, '提交后未确认登录成功'):
                worker.open_account(account)
            ask.assert_not_called()


if __name__ == '__main__':
    unittest.main()


class RangeQuerySkipsEmptyMonths(unittest.TestCase):
    """范围查询逐行下载：某月无行时跳过、不拖垮整任务；有行的月照常下载。"""

    class _Fake:
        def __init__(self):
            self.opened = False
            self.last_download_name = ''
            self.last_detail_claims = {}
            self.clicks = []

        def open_url(self, url):
            self.opened = True

        def wait(self, predicate, timeout=20, interval=.5):
            return predicate()

        def js(self, code):
            if '已生成完成' in code:
                return True
            if 'selectors.every' in code:               # _bill_inputs_ready
                return True
            if 'PENDING=' in code:                      # _HISTORY_ROW_JS（历史记录该行已完成）
                return 'span:nth-of-type(2)>a:nth-of-type(1)' if '2026-04 ~ 2026-04' in code else ''
            if 'map(s=>document.querySelector' in code:
                return ['2026-03', '2026-04']
            if 'e?e.value' in code:                     # 月份输入框回读：第一次=开始，第二次=结束
                self._readbacks = getattr(self, '_readbacks', 0) + 1
                return '2026-04' if self._readbacks % 2 == 0 else '2026-03'
            if '!!document.querySelector' in code or 'e.focus' in code:
                return True
            if 'want.some' in code:                       # _any_target_present / _wait_any_target_row
                if '"202603","202604"' in code:
                    return True
                if '"202603"]' in code:
                    return False                         # 2026-03 无行
                if '"202604"]' in code:
                    return True
                return False
            return ''

        def key_escape(self):
            pass

        def click_text(self, label, row=None, timeout=20, scope_css=None):
            self.clicks.append((label, row, scope_css))
            return 'sel'

        def click(self, sel, label='按钮'):
            self.clicks.append(('click', sel, label))

        def _mouse_click(self, sel, manage_focus=True):
            pass

        def call(self, action, args=None, timeout=120):
            return {}

        def download(self, trigger, folder, name, timeout=180):
            trigger()
            self.last_download_name = '交易货款_202604_202604.csv'
            p = Path(folder) / (name + '.csv')
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('data')
            return p

    def test_bill_income_skips_month_with_no_row(self):
        b = self._Fake()
        with patch.object(tmall_bridge.time, 'sleep'):
            paths = tmall_bridge.fetch_source(b, 'bill_income',
                                              ['2026-03', '2026-04'], Path('/tmp/cb-range-test'))
        # 只有 2026-04 下到了；2026-03 无行被跳过。
        self.assertEqual(list(paths.keys()), ['2026-04'])
        gen = [c for c in b.clicks if c[0] == '下载全量明细']
        self.assertEqual([c[1] for c in gen], ['202604'],
                         '只为有数据的月份点过「下载全量明细」')
        query = [c for c in b.clicks if c[0] == tmall_bridge._BILL_QUERY_LABELS]
        self.assertEqual(query[0][2], tmall_bridge._BILL_FILTER_SCOPE)

    def test_history_wait_polls_until_download_appears(self):
        """历史记录该行「进行中」时（PENDING）必须一直等，出现「下载」才取。"""
        browser = Mock()
        browser.js.side_effect = ['PENDING=2', 'span:nth-of-type(2)>a:nth-of-type(1)']
        browser._found_selector = Mock(return_value=None)
        with patch.object(tmall_bridge.time, 'sleep'), \
                patch.object(tmall_bridge.time, 'monotonic',
                             side_effect=[0, 0]):
            hist = tmall_bridge._wait_history_done(browser, '2026-08')
        self.assertEqual(hist, 'span:nth-of-type(2)>a:nth-of-type(1)')
        browser._found_selector.assert_not_called()

    def test_history_wait_turns_pages_when_row_is_not_on_page_one(self):
        """该月的行被挤出第一页时，要翻页去找，而不是在第一页傻等。"""
        browser = Mock()
        browser.js.side_effect = ['N=0', 'page1', 'page1', False, 'page2',
                                  'span:nth-of-type(2)>a:nth-of-type(1)']
        clicks = []

        def found_selector(label, row=None):
            return f'btn-{label}' if label == '下一页' else None

        browser._found_selector = Mock(side_effect=found_selector)
        browser.click = Mock(side_effect=lambda path, label='按钮': clicks.append(label))
        with patch.object(tmall_bridge.time, 'sleep'), \
                patch.object(tmall_bridge.time, 'monotonic',
                             side_effect=[0, 0, 100, 100]):
            hist = tmall_bridge._wait_history_done(browser, '2026-07')
        self.assertEqual(hist, 'span:nth-of-type(2)>a:nth-of-type(1)')
        self.assertEqual(clicks, ['下一页'], '恰好翻一页就找到了 7 月的行')

    def test_history_cannot_loop_forever_while_returning_to_first_page(self):
        browser = Mock()
        browser.js.return_value = 'some-other-page'
        browser._found_selector.return_value = '#previous'
        with patch.object(tmall_bridge.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, '无法确认已返回第一页'):
                tmall_bridge._history_back_to_first_page(browser, 'first-page')
        self.assertEqual(browser.click.call_count, 100)

    def test_history_wait_gives_up_after_timeout(self):
        """一直「进行中」也不能无限等：超时报可继续的错误，而不是卡死。"""
        browser = Mock()
        browser.js.return_value = 'PENDING=2'
        browser._found_selector = Mock(return_value=None)
        with patch.object(tmall_bridge.time, 'sleep'), \
                patch.object(tmall_bridge.time, 'monotonic',
                             side_effect=lambda: float('inf')):
            with self.assertRaisesRegex(RuntimeError, '进行中'):
                tmall_bridge._wait_history_done(browser, '2026-08', timeout=5)
