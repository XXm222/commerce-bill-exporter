#!/usr/bin/env python3
"""聚胜算订单商品原始 CSV 导出。使用用户已安装 Chrome 的独立账号登录态。"""
import json
import os
import time
import uuid

PAGE = 'https://ss.erp321.com/profit-report/order-profit'
# 等页面就绪的上限。抽成常量是为了让测试能把它压到很小。
OPEN_TIMEOUT = 30
# 就绪必须**连续成立**这么久才算数，以及轮询间隔。理由见 open()：会话过期时页面会先就绪、
# 约 0.3 秒后才跳登录页，单次探测会把这种「假就绪」当成可用（真实站点上实测到过）。
READY_SETTLE = 1.5
POLL = .5

# 网站改版时的统一说法。模块编号是构建产物，平台一重新发布就可能变，
# 这时页面其实是登录着的——若沿用「请完成登录」的提示，用户会反复去登录，
# 永远解决不了问题，而真正要做的是更新适配器。
CHANGED_HINT = '网站接口模块已变更，需要更新适配器后继续'

# 就绪探测。三种状态：ready（可用）/ changed（登录了但接口模块变了）/ modules=false（登录了但找不到模块编号）。
# 与 测试/check_jst_adapter.py 共用同一份代码：工具里不再抄一遍等价实现，
# 否则工具说「没问题」而驱动失败时，无法判断是页面变了还是两份判断不一致。
READY_JS = '''(()=>{
    if(location.origin !== 'https://ss.erp321.com') return JSON.stringify({ready:false});
    const chunks=window.webpackChunkshengsuan;
    if(!chunks) return JSON.stringify({ready:false});
    chunks.push([[Date.now()],{},r=>window.__jstBillRequire=r]);
    const r=window.__jstBillRequire;
    if(!r.m[71124] || !r.m[72432]) return JSON.stringify({modules:false});
    if(!r.m[72432].toString().includes('/WebApi/PF/AdaptiveExport/ExprotPfOrderSku'))
        return JSON.stringify({changed:true});
    return JSON.stringify({ready:typeof r(71124).WY==='function'});
})()'''
ENDPOINTS = {
    'shops': '/WebApi/PF/Shop/GetShopDrop',
    'account': '/WebApi/PF/Main/GetPfMainInfo',
    'query': '/WebApi/PF/OrderSku/GetOrderSkuList',
    'export': '/WebApi/PF/AdaptiveExport/ExprotPfOrderSku',
    'tasks': '/WebApi/PF/NightPlan/GetNightList',
}


def write_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.chmod(temp, 0o600)
    temp.replace(path)


class Browser:
    def __init__(self, rpc):
        self.rpc = rpc

    def call(self, action, args=None):
        return self.rpc(action, args or {})

    def js(self, code):
        # 适配器读写页面主世界全局（window.webpackChunkshengsuan / __jstBillJobs），
        # 隔离世界看不到这些对象，必须走主世界。
        result = self.call('evaluate', {'code': code, 'mainWorld': True})
        if result.get('type') != 'string':
            raise RuntimeError('页面执行失败或尚未登录，请在打开的 Chrome 中完成聚水潭登录')
        return json.loads(result['value'])

    def open(self):
        """等报表页**真正**可用。

        「就绪」不能只探测一次就算数。2026-09-28 在真实站点上实测（APP 自己的 Chrome 目录、
        会话已过期）：页面先加载出报表页的脚本（webpack 全局与模块映射都在，READY_JS 返回
        ready:true），约 0.3 秒后才跳转到 jstlogin.erp321.com。于是单次探测让 open() 在 0.6 秒
        「顺利返回」，而那一刻页面马上就要离开报表域名——接着 shops()/query() 会落在一个正在
        导航的页面上，最坏的情况是导出请求已经发出却拿不到结果（任务变成「结果未知」，
        而明知会话是过期的，这本来根本不该走到那一步）。

        所以这里要求就绪**连续成立 READY_SETTLE 秒**才返回：真就绪只多等这一会儿；
        假就绪（首探就绪、随即跳转）会被下一次探测识破，最终按「未登录」如实报错。
        """
        try:
            self.call('find_tab', {'url': PAGE})
        except RuntimeError:
            self.call('navigate', {'url': PAGE, 'newTab': True, 'group_title': '电商账单导出'})
        deadline = time.monotonic() + OPEN_TIMEOUT
        changed = False
        ready_since = None
        while time.monotonic() < deadline:
            status = self.js(READY_JS)
            if status.get('changed'):
                raise RuntimeError(CHANGED_HINT)
            now = time.monotonic()
            if status.get('ready'):
                if ready_since is not None and now - ready_since >= READY_SETTLE:
                    return
                if ready_since is None:
                    ready_since = now
            else:
                ready_since = None
                # modules=false 只在 webpack 全局存在时才会返回，也就是「已经登录了」。
                # 先记下来，等超时再据此区分「没登录」和「网站改了」。
                if status.get('modules') is False:
                    changed = True
            time.sleep(POLL)
        if changed:
            raise RuntimeError('已登录聚水潭，但' + CHANGED_HINT)
        raise RuntimeError('页面未准备好。请在打开的 Chrome 中完成聚水潭登录后重试')

    def api(self, operation, body, timeout=90):
        for attempt in range(3):
            try:
                return self._api_once(operation, body, timeout)
            except (RuntimeError, TimeoutError):
                if operation == 'export' or attempt == 2:
                    raise
                time.sleep(attempt + 1)

    def _api_once(self, operation, body, timeout):
        # 后台执行并轮询，避免导出请求因工具响应超时被重复提交。
        job = uuid.uuid4().hex
        route = ENDPOINTS[operation]
        main_filter = "data={companyId:r.data?.companyInfo?.coId,userId:r.data?.userInfo?.userId};" if operation == 'account' else ''
        code = '''(()=>{
          const jobs=window.__jstBillJobs||(window.__jstBillJobs={});
          const id=JOB; jobs[id]={state:'running'};
          window.__jstBillRequire(71124).WY(ROUTE,{method:'POST',data:BODY})
          .then(r=>{let data=r.data;FILTER jobs[id]={state:'done',value:{code:r.code,msg:r.msg,data,page:r.page}};})
          .catch(()=>{jobs[id]={state:'error',error:'平台请求失败，请检查页面登录状态及权限'};});
          return JSON.stringify({started:true});
        })()'''.replace('JOB', json.dumps(job)).replace('ROUTE', json.dumps(route)).replace('BODY', json.dumps(body)).replace('FILTER', main_filter)
        self.js(code)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.js('JSON.stringify(window.__jstBillJobs?.[' + json.dumps(job) + ']||{state:"lost"})')
            if status['state'] == 'done':
                result = status['value']
                self.js('(()=>{delete window.__jstBillJobs['+json.dumps(job)+'];return JSON.stringify({ok:true})})()')
                if result.get('code') != 0:
                    raise RuntimeError(f"平台返回错误 {result.get('code')}: {result.get('msg')}")
                return result
            if status['state'] in ('error', 'lost'):
                raise RuntimeError(status.get('error', '浏览器页面已重载，无法确认请求结果'))
            time.sleep(1)
        raise TimeoutError('平台请求超过等待时间，结果未知。导出请求不会自动重提')


def condition(start, end, shop_id, date_field, back_goods, split_combine):
    # 来源：2026-09-27 页面原生 an(formValues) 的转换结果。
    c = {k: [] for k in ('costTypeList', 'orderType', 'drpCoidtos', 'wmsCoidList',
                         'buIdList', 'creatorList', 'cusIdList', 'brands', 'categorys',
                         'vcNames', 'skuCostAmountList', 'stateCityList')}
    for key, kind, select in (('skuIn','anyonesku','in'),('skuOut','anyonesku','out'),
                              ('labelIn','anyonelabel','in'),('labelout','anyonelabel','out')):
        c[key] = {'type': kind, 'values': [], 'select': select}
    c.update(type='oid', dateFld=date_field, shop=[shop_id], beginDate=start, endDate=end,
             groupByType=2, skuCostAmountSearchType=2, firstfreightList=[''],
             backGoodsType=back_goods, splitCombine=split_combine, isOL=True,
             skugroup='pf_o_id', isCrossBorder=False, isValidateDate=False)
    return c


def shops(browser):
    data = browser.api('shops', {'data': {'isCm': False, 'isCrossBorder': False,
                                         'isContainDisabled': False}})['data']
    return [{'id': s['id'], 'name': s['name'], 'platform': s.get('site','')}
            for s in data['shopList']]


def query(browser, c):
    result = browser.api('query', {'data': {'condition': c},
                                  'page': {'currentPage': 1, 'pageSize': 25}})
    count = result.get('page', {}).get('count')
    if not isinstance(count, int) or count < 0:
        raise RuntimeError('查询未返回有效订单数量')
    return count


def wait_task(browser, task_id, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        page = 1
        while True:
            # 不限制 versionModel。订单商品数据导出任务不属于 12 号类型；固定传 12
            # 会只返回旧的少数任务，刚提交的任务即使 Success 也永远找不到。
            r = browser.api('tasks', {'data': {'cbShopSite': '', 'useOldTaskTable': False},
                                      'page': {'currentPage': page, 'pageSize': 100}})
            rows = r.get('data')
            if not isinstance(rows, list):
                raise RuntimeError('异步任务列表格式已改变')
            for task in rows:
                if str(task.get('tId')) == str(task_id):
                    if task.get('status') == 'Success':
                        if not task.get('fileUrl'):
                            raise RuntimeError('异步任务成功但没有下载链接')
                        return task['fileUrl']
                    if task.get('status') in ('Failure','TimeOut','Cancel'):
                        raise RuntimeError(f"异步任务 {task_id}: {task.get('status')} {task.get('err') or ''}")
                    break
            else:
                count = r.get('page', {}).get('count', len(rows))
                if page * 100 < count:
                    page += 1
                    continue
            break
        time.sleep(min(5, max(0, deadline-time.monotonic())))
    raise TimeoutError(f'异步任务 {task_id} 仍未完成，使用同一目录重跑可继续等待')
