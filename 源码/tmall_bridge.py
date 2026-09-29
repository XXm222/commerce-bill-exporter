"""Tmall's two monthly exports through the user's running Chrome.

Range query, per-row download
----------------------------
Multi-month tasks do ONE range query (开始=起点 / 结束=终点, or #billCycle range),
then download each month's row by scoping the row's download button to that month's
text.  Months with no data simply don't have a row and are skipped — they no longer
abort the whole task.  This matches the page's native behaviour (one query, one row
per month) and avoids the per-month page reload that used to die on empty months.
"""
import json
import time

import tmall_export as rules

_BILL_FILTER_SCOPE = 'form:has(input[placeholder="开始月份"]):has(input[placeholder="结束月份"])'
_BILL_QUERY_LABELS = ('查询', 'Search')


_CALENDAR_CELL_JS = '''(()=>{const want=__WANT__,year=__YEAR__;
 const norm=t=>(t||'').replace(/[\\s\\u00a0]+/g,' ').trim();
 const panels=[...document.querySelectorAll('.next-calendar2-panel')].filter(e=>e.getClientRects().length);
 const panel=panels.find(p=>norm((p.querySelector('.next-calendar2-header')||{}).textContent).includes(year));
 if(!panel)return 'YEAR=0';
 const nodes=[...panel.querySelectorAll('.next-calendar2-cell-value,td,span,div')];
 const cells=nodes.filter(e=>e.getClientRects().length&&norm(e.textContent)===want)
   .filter(e=>!nodes.some(x=>x!==e&&e.contains(x)&&norm(x.textContent)===want));
 if(cells.length!==1)return 'CELL='+cells.length;
 let cur=cells[0],parts=[];
 while(cur&&cur.nodeType===1&&parts.length<12){let n=1,s=cur;
   while((s=s.previousElementSibling))if(s.tagName===cur.tagName)n++;
   parts.unshift(cur.tagName.toLowerCase()+':nth-of-type('+n+')');cur=cur.parentElement;}
 return parts.join('>');})()'''


def _targets_present_js(months):
    targets = [rules.month_compact(m) for m in months]
    return '''(()=>{const want=__WANT__;
      return [...document.querySelectorAll('tr')].some(e=>e.getClientRects().length&&
        want.some(m=>(e.innerText||'').replace(/[\\s\\u00a0]+/g,' ').includes(m)));})()'''.replace('__WANT__', json.dumps(targets))


def _any_target_present(browser, months):
    try:
        return bool(browser.js(_targets_present_js(months)))
    except RuntimeError:
        return False


def _row_present(browser, month):
    return _any_target_present(browser, [month])


def _wait_any_target_row(browser, months, timeout=30):
    """等范围查询返回任意目标月份行，并做落定确认（聚水潭链路同款防假就绪）。"""
    js = _targets_present_js(months)

    def found():
        try:
            return browser.js(js)
        except RuntimeError:
            return None
    if not browser.wait(found, timeout):
        raise RuntimeError(f'范围查询没有返回任何目标月份行（{months[0]}~{months[-1]}），已停止')
    time.sleep(1.0)
    if not browser.wait(found, 8):
        raise RuntimeError('范围查询结果出现后又消失（表格仍在刷新），已停止以避免下载到其它查询的结果')


def _wait_month_row(browser, month, history=False, timeout=25):
    """历史下载记录页定位某月那一行（不是查询结果，不做落定确认）。"""
    target = rules.month_range_text(month) if history else rules.month_compact(month)

    def found():
        return browser.js('''(()=>{const wanted=__WANT__;
            return [...document.querySelectorAll('tr')].some(e=>e.getClientRects().length&&
              (e.innerText||'').replace(/[\\s\\u00a0]+/g,' ').includes(wanted));})()'''.replace('__WANT__', json.dumps(target)))
    if not browser.wait(found, timeout):
        raise RuntimeError(f'天猫表格里没有 {month} 的明细行，已停止以避免导出错误月份')
    return target


# 历史记录的规则（2026-09-28/29 用户实测说明）：
#   - 一个月在历史里**只有一行**；以前生成过的话，再点「下载全量明细」只会提示
#     「已生成」让去历史记录取，不会产生重复行；
#   - 点「下载全量明细」后生成需要**不定长时间**，期间该行显示「进行中」、没有「下载」；
#   - 所以取文件按「时间范围」列匹配那一行，等它出现「下载」再点。
_HISTORY_ROW_JS = '''(()=>{const want=__WANT__;
  const norm=t=>(t||'').replace(/[\\s\\u00a0]+/g,' ').trim();
  const rows=[...document.querySelectorAll('tr')].filter(e=>e.getClientRects().length&&
    norm(e.innerText).includes(want));
  if(!rows.length)return 'N=0';
  const row=rows[0];
  const nodes=[...row.querySelectorAll('a,button,span,div')].filter(e=>
    e.getClientRects().length&&norm(e.textContent)==='下载');
  const link=nodes.find(e=>!nodes.some(x=>x!==e&&e.contains(x)&&norm(x.textContent)==='下载'));
  if(!link)return 'PENDING='+rows.length;
  let cur=link,parts=[];
  while(cur&&cur.nodeType===1&&parts.length<12){let n=1,s=cur;
    while((s=s.previousElementSibling))if(s.tagName===cur.tagName)n++;
    parts.unshift(cur.tagName.toLowerCase()+':nth-of-type('+n+')');cur=cur.parentElement;}
  return parts.join('>');})()'''


def _wait_history_done(browser, month, timeout=300, checkpoint=None):
    """等历史记录里该月那一行变成「已完成」（出现「下载」），返回它的 CSS 路径。

    该月只有一行；生成期间显示「进行中」、没有「下载」链接（PENDING），一直等到
    出现为止。生成耗时不确定，默认最多等 300 秒。等待中每秒都检查停止标记。
    历史记录分页且按创建时间倒序：该月以前生成过、后来又被别的月份挤出第一页时，
    会逐页往后翻着找（_history_turn_page）；翻遍都没有就回第一页等生成——
    新生成的行会出现在第一页顶部。
    """
    js = _HISTORY_ROW_JS.replace('__WANT__', json.dumps(rules.month_range_text(month)))
    deadline = time.monotonic() + timeout
    first_page = None
    seen_pages = set()
    while True:
        if checkpoint:
            checkpoint()
        try:
            value = browser.js(js)
        except RuntimeError:
            value = None
        if value and not str(value).startswith(('N=', 'PENDING=')):
            return value
        if value and str(value).startswith('PENDING='):
            # This month's row is present but still generating.  Stay on the
            # same page; paging away could pick up a different month's link.
            if time.monotonic() >= deadline:
                raise RuntimeError(f'天猫历史记录里 {month} 等了 {timeout} 秒仍是「进行中」。'
                                   '生成可能还在进行，可稍后在导出记录里继续')
            time.sleep(1.0)
            continue
        if first_page is None:
            try:
                first_page = browser.js(_PAGE_FIRST_ROW_JS) or ''
            except RuntimeError:
                first_page = ''
        if not _history_turn_page(browser, seen_pages):
            # 已翻到最后一页都没有：回第一页等生成（新生成的行出现在第一页顶部）。
            _history_back_to_first_page(browser, first_page)
        if time.monotonic() >= deadline:
            raise RuntimeError(f'天猫历史记录里 {month} 等了 {timeout} 秒仍是「进行中/不存在」。'
                               '生成可能还在进行，可稍后在导出记录里继续')
        time.sleep(1.0)


_PAGE_FIRST_ROW_JS = '''(()=>{const rows=[...document.querySelectorAll('tbody tr')]
  .filter(e=>e.getClientRects().length&&e.querySelector('td'));
  const norm=t=>(t||'').replace(/[\\s\\u00a0]+/g,' ').trim();
  return rows.length?norm(rows[0].innerText).slice(0,160):'';})()'''


def _history_turn_page(browser, seen_pages):
    """点「下一页」翻一页找目标行；返回是否真的翻到了没看过的新页。"""
    try:
        fingerprint = browser.js(_PAGE_FIRST_ROW_JS) or ''
    except RuntimeError:
        return False
    if fingerprint in seen_pages:
        return False                    # 这一页搜过：已到尽头或绕了一圈
    seen_pages.add(fingerprint)
    path = None
    try:
        path = browser._found_selector('下一页', None)
    except (RuntimeError, AttributeError):
        path = None
    if not path:
        return False
    # 真实按钮形状（2026-09-29 真机核对）：<button class="next-btn ... next-next" disabled>
    # 里包着 span.next-btn-helper。禁用时不发无谓的真实点击。
    try:
        disabled = browser.js(f'''(()=>{{const e=document.querySelector({json.dumps(path)});
          const b=e&&(e.closest('button,[role=button]')||e);
          return !!(b&&(b.disabled||b.getAttribute('aria-disabled')==='true'));}})()''')
    except RuntimeError:
        disabled = False
    if disabled:
        return False
    try:
        browser.click(path, '下一页')
    except RuntimeError:
        return False
    time.sleep(.6)
    try:
        new_fingerprint = browser.js(_PAGE_FIRST_ROW_JS) or ''
    except RuntimeError:
        return False
    return bool(new_fingerprint) and new_fingerprint != fingerprint


def _history_back_to_first_page(browser, first_page):
    """点「上一页」回到第一页（翻页找完没找到后，回第一页等新生成的行）。"""
    for _ in range(100):
        try:
            fingerprint = browser.js(_PAGE_FIRST_ROW_JS) or ''
        except RuntimeError:
            return
        if first_page and fingerprint == first_page:
            return
        path = None
        try:
            path = browser._found_selector('上一页', None)
        except (RuntimeError, AttributeError):
            path = None
        if not path:
            return
        try:
            browser.click(path, '上一页')
        except RuntimeError:
            return
        time.sleep(.4)
    raise RuntimeError('天猫历史记录翻页超过 100 页，无法确认已返回第一页，已停止')


def _type_bill_field(browser, selector, month):
    """逐字敲入一个月份输入框 + Enter 提交。

    Fusion 月份选择器只认真实按键事件：daemon 的 fill 直接写 input.value，读回看着
    对但选择器内部状态没更新，查询时仍用默认/上月。逐字敲键 + Enter 才真正吃进去。
    """
    if not browser.wait(lambda: browser.js(f'!!document.querySelector({json.dumps(selector)})'), 5):
        raise RuntimeError('天猫月份输入框在填写前消失，已停止')
    browser.call('cdp', {'method': 'Emulation.setFocusEmulationEnabled',
                         'params': {'enabled': True}})
    got = None
    try:
        for _ in range(3):
            browser._mouse_click(selector, manage_focus=False)
            browser.js(f'(()=>{{const e=document.querySelector({json.dumps(selector)});if(e){{e.focus();e.select();}}return !!e}})()')
            # 清掉旧值与可能的自动填充（2026-09 这种默认值会卡住选择器）。
            browser.call('send_keys', {'keys': 'Mod+A'})
            browser.call('send_keys', {'keys': 'Backspace'})
            browser.call('key_type', {'text': month, 'delay': 90})
            # Enter 让 Fusion 月份选择器把输入值固化进内部状态；不提交就读回的是
            # 裸字符串，查询时选择器仍用旧状态——正是「日期没选对」的根因。
            browser.call('send_keys', {'keys': 'Enter'})
            time.sleep(.4)
            got = browser.js(f'(()=>{{const e=document.querySelector({json.dumps(selector)});return e?e.value:null}})()')
            if got == month:
                break
            time.sleep(.5)
    finally:
        browser.call('cdp', {'method': 'Emulation.setFocusEmulationEnabled',
                             'params': {'enabled': False}})
    if got != month:
        raise RuntimeError(f'天猫月份输入框没有接受 {month}，读回的是 {got!r}（已逐字输入并提交）')


def _bill_inputs_ready(browser):
    selectors = {label: f'input[placeholder="{label}"]'
                 for label in ('开始月份', '结束月份')}

    def ready():
        return browser.js('''(()=>{const selectors=__SELECTORS__;
          return selectors.every(s=>{const e=document.querySelector(s);
            return !!e&&e.getClientRects().length>0&&!e.disabled;});})()'''
                          .replace('__SELECTORS__', json.dumps(list(selectors.values()), ensure_ascii=False)))
    return selectors, ready


def _set_bill_inputs(browser, start, end):
    """填收支账单的开始/结束月份（可不同）。先等控件挂载，再逐字填、回读校验。"""
    selectors, ready = _bill_inputs_ready(browser)
    # document.readyState becomes complete before the merchant SPA mounts its
    # monthly controls.  A second navigation to the same bill URL also clears
    # them briefly.  Never type until both controls are really on screen.
    if not browser.wait(ready, 20):
        for tab in ('收入账单', '月汇总'):
            try:
                browser.click_text(tab, timeout=5)
            except RuntimeError:
                pass
        if not browser.wait(ready, 15):
            location = browser.js('location.hostname+location.pathname') or '未知页面'
            placeholders = browser.js('''[...document.querySelectorAll('input')]
              .filter(e=>e.getClientRects().length).map(e=>e.placeholder).filter(Boolean).slice(0,8)''') or []
            raise RuntimeError(f'天猫收支账单页面没有加载出开始/结束月份输入框（当前：{location}；'
                               f'可见输入框：{placeholders}）。已停止，避免导出错误账期')
    _type_bill_field(browser, selectors['开始月份'], start)
    _type_bill_field(browser, selectors['结束月份'], end)
    values = browser.js('''(()=>__SELECTORS__.map(s=>document.querySelector(s)?.value||null))()'''
                        .replace('__SELECTORS__', json.dumps(list(selectors.values()), ensure_ascii=False)))
    if values != [start, end]:
        raise RuntimeError(f'天猫月份范围未保持为 {start} ~ {end}（读回 {values!r}），已停止')


def _set_bill_month(browser, month):
    """单月兼容入口（旧调用方/测试用）：起止都填该月。"""
    _set_bill_inputs(browser, month, month)


def _calendar_cell_selector(browser, month):
    year, number = month.split('-')
    code = _CALENDAR_CELL_JS.replace('__WANT__', json.dumps(f'{int(number)}月')).replace('__YEAR__', json.dumps(f'{year}年'))
    return browser.js(code)


def _set_fund_range(browser, start, end):
    """聚核算 #billCycle 入账日期范围选择器：点起格再点止格（同月则点两次同格）。"""
    try:
        browser.click_text('入账日期', timeout=5)
    except RuntimeError:
        browser.click('#billCycle', '入账日期')
    if not browser.wait(lambda: browser.js("!!document.querySelector('.next-calendar2-panel')"), 10):
        raise RuntimeError('聚核算月份选择器没有展开')
    start_sel = _calendar_cell_selector(browser, start)
    end_sel = _calendar_cell_selector(browser, end)
    bad = lambda s: not s or str(s).startswith(('YEAR=', 'CELL='))
    if bad(start_sel) or bad(end_sel):
        raise RuntimeError(f'聚核算月份面板找不到 {start} 或 {end}（{start_sel} / {end_sel}）')
    if start_sel != end_sel:
        browser.click(start_sel, f'{start}起')
        time.sleep(.4)
        browser.click(end_sel, f'{end}止')
        time.sleep(.4)
    else:
        # 单月：同格点两次得到单月范围。
        browser.click(start_sel, start)
        time.sleep(.4)
        browser.click(start_sel, start)
        time.sleep(.4)
    # The date picker may close after the second click.  Read the two inputs
    # from the control, not the calendar cells, before searching.
    values = browser.js("[...document.querySelectorAll('#billCycle input')].map(e=>e.value)") or []
    if len(values) < 2 or values[:2] != [start, end]:
        raise RuntimeError(f'聚核算入账日期没有设成 {start}~{end}（读回 {values}）')


def _set_fund_month(browser, month):
    """单月兼容入口（旧调用方/测试用）。"""
    _set_fund_range(browser, month, month)


def _ensure_range_results(browser, months):
    """关掉历史记录抽屉、回到查询结果表；若范围查询丢失，重点一次查询拉回来。"""
    closer = getattr(browser, 'key_escape', None)
    if closer:
        # 历史记录是覆盖在页面上的抽屉，不关掉会挡住「月汇总」标签。
        try:
            closer()
        except RuntimeError:
            pass
    try:
        browser.click_text('月汇总', timeout=5)
    except RuntimeError:
        pass
    if not _any_target_present(browser, months):
        # 范围输入仍在（_set_bill_inputs 设过），重点一次查询把表格拉回来。
        try:
            browser.click_text(_BILL_QUERY_LABELS, timeout=5, scope_css=_BILL_FILTER_SCOPE)
        except RuntimeError:
            pass
        _wait_any_target_row(browser, months)


def fetch_source(browser, key, months, folder, notify=None, checkpoint=None):
    """对一组月份做一次范围查询、逐行下载；返回 {month: path}。

    一次查 [起点, 终点]，表格里每个月一行；没数据的月份不出行，自动跳过、不报错、
    不拖垮整任务。逐行按月份文本定位该行的下载按钮（click_text 的 row= 参数），
    避免点错月份。fund_detail 的页面声明数字（明细笔数/收入金额）按月收集到
    browser.last_detail_claims，供 Engine 与下载文件核对。
    """
    flow = dict(rules.FLOWS[key], key=key)
    if checkpoint:
        checkpoint()
    browser.open_url(flow['url'])
    paths = {}
    if key == 'bill_income':
        _set_bill_inputs(browser, months[0], months[-1])
        browser.click_text(_BILL_QUERY_LABELS, scope_css=_BILL_FILTER_SCOPE)
        _wait_any_target_row(browser, months)
        if notify:
            notify(f'{flow["name"]}：范围查询已返回 {months[0]}~{months[-1]}')
        for month in months:
            if checkpoint:
                checkpoint()
            _ensure_range_results(browser, months)
            if not _row_present(browser, month):
                if notify:
                    notify(f'{flow["name"]}：{month} 无明细行，跳过')
                continue
            row = rules.month_compact(month)
            browser.click_text('下载全量明细', row=row)
            # The first click generates a file on the site (or, if that month was
            # generated before, just points to the history); Chrome receives the
            # file only after the history row finishes and is downloaded.
            browser.wait(lambda: browser.js("(document.body.innerText||'').includes('已生成完成')"), 30)
            browser.click_text('历史下载记录')
            # 同月可能有多行（以前导出过），且最新一行可能还在「进行中」：
            # 只认时间范围匹配的最新一行，等它出现「下载」再点。
            hist = _wait_history_done(browser, month, checkpoint=checkpoint)
            path = browser.download(lambda h=hist: browser.click(h, '下载'), folder, f'{key}-{month}')
            paths[month] = path
            rules.verify_download_name(browser, flow, month)
            if notify:
                notify(f'{flow["name"]}：已下载 {month} 明细 {browser.last_download_name}')
    elif key == 'fund_detail':
        browser.click_text('月汇总')
        _set_fund_range(browser, months[0], months[-1])
        browser.click_text('搜索')
        _wait_any_target_row(browser, months)
        if notify:
            notify(f'{flow["name"]}：范围查询已返回 {months[0]}~{months[-1]}')
        claims_map = {}
        for month in months:
            if checkpoint:
                checkpoint()
            if not _row_present(browser, month):
                if notify:
                    notify(f'{flow["name"]}：{month} 无明细行，跳过')
                continue
            row = rules.month_compact(month)
            claims = rules.read_row_claims(browser, flow, month)
            if claims:
                claims_map[month] = {'key': key, 'month': month, **claims}
            path = browser.download(lambda r=row: browser.click_text('下载明细', row=r), folder, f'{key}-{month}')
            paths[month] = path
            rules.verify_download_name(browser, flow, month)
            if notify:
                notify(f'{flow["name"]}：已下载 {month} 明细 {browser.last_download_name}')
        browser.last_detail_claims = claims_map
    else:
        raise RuntimeError(f'未知天猫报表来源：{key}')
    return paths


def fetch_month(browser, key, month, folder, notify=None, checkpoint=None):
    """单月兼容入口（旧调用方/测试用）：等价于 fetch_source 只取一个月。"""
    return fetch_source(browser, key, [month], folder, notify, checkpoint).get(month)
