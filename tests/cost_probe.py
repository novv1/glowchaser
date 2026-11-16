"""零额度成本探针：算出「一次查询会花掉多少 Open-Meteo 额度」。

用法::

    python tests/cost_probe.py                      # 默认：珠三角一点，后天
    python tests/cost_probe.py --lat 22.27 --lon 113.58 --date 2026-10-10

为什么需要它：Open-Meteo 免费层按 IP 计 `calls/day`，而一次调用不等于一次
HTTP —— 官方 pricing 页内嵌计算器的口径是

    base  = (variables * models) / 10
    calls = max(1, base * max(1, days / 14) * locations)

也就是说**坐标数与变量、模式数一样参与放大**，而且 `days <= 14` 时不额外计费
（`lengthFactor` 恒为 1），所以「缩短天数」省不了额度、只有「减少坐标/变量/模式」
才省。这份探针让编排成本随时可复核，避免再靠估算。

它**不发任何真实请求、也不写任何真实数据**：
① `om._get`（唯一出站接缝）被换成记录器；
② `terrain._elev_cache_save` 与 `forecast_log._save` 被替换为 no-op，
   免得假海拔写进 `data/elev_cache.json`、假预测写进 `data/forecast_log.json`。
其余取数编排、参数拼装全部走真实代码，因此拿到的就是真实会发出去的请求参数。
"""
import inspect
import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ['GC_OM_CACHE_DIR'] = ''          # 不读写磁盘缓存：测的是冷启动成本

import app.services.openmeteo as om          # noqa: E402
import app.services.report as rpt            # noqa: E402
import app.services.terrain as terrain       # noqa: E402

DEFAULT_LAT, DEFAULT_LON = 22.2710, 113.5767


class _MP:
    """最小 monkeypatch 替身（探针不依赖 pytest）。"""

    def __init__(self):
        self._undo = []

    def setattr(self, obj, name, val):
        self._undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, val)

    def undo(self):
        for obj, name, val in reversed(self._undo):
            setattr(obj, name, val)
        self._undo = []


RECORDS = []


def _days_of(params):
    if 'start_date' in params:
        a = datetime.strptime(params['start_date'], '%Y-%m-%d')
        b = datetime.strptime(params.get('end_date') or params['start_date'],
                              '%Y-%m-%d')
        return (b - a).days + 1
    if 'forecast_days' in params:
        return int(params['forecast_days'])
    return 1


def _weighted(url, params):
    """官方计算器口径；同时返回明细项，便于人工核对。"""
    loc = len(str(params.get('latitude', '')).split(','))
    if 'elevation' in url:
        var, models, days = 1, 1, 1
    else:
        var = len([v for v in str(params.get('hourly', '')).split(',') if v])
        models = len([m for m in str(params.get('models', '')).split(',') if m]) or 1
        days = _days_of(params)
    base = (var * models) / 10.0
    lf = max(1.0, days / 14.0)
    return max(1.0, base * lf * loc), loc, var, models, days


def spy(url, params, timeout=40):
    w, loc, var, models, days = _weighted(url, params)
    RECORDS.append({'url': url, 'loc': loc, 'var': var, 'models': models,
                    'days': days, 'weighted': w})
    # 返回结构合法即可；取值真假不影响成本统计
    t = ['%sT%02d:00' % (params.get('start_date',
                                    datetime.now().strftime('%Y-%m-%d')), h)
         for h in range(24)]
    if 'elevation' in url:
        return {'elevation': [10.0] * loc}
    item = {'hourly': {'time': t}}
    for v in [x for x in str(params.get('hourly', '')).split(',') if x]:
        for m in [x for x in str(params.get('models', '')).split(',') if x] or ['_']:
            item['hourly']['%s_%s' % (v, m)] = [40.0] * len(t)
        item['hourly'][v] = [40.0] * len(t)
    return item if loc == 1 else [item for _ in range(loc)]


def _install(mp):
    mp.setattr(om, '_get', spy)
    mp.setattr(terrain, '_elev_cache_save', lambda new: None)
    mp.setattr(rpt.flog, '_save', lambda data: None)


def _dump(title):
    total = sum(r['weighted'] for r in RECORDS)
    raw = len(RECORDS)
    print('')
    print('=== %s ===' % title)
    print('raw requests = %d, weighted(calculator) = %.1f, FAQ-style = %d request(s)'
          % (raw, total, raw))
    print('%-38s %4s %4s %4s %5s %9s' % ('url', 'loc', 'var', 'mod', 'days', 'weighted'))
    for r in RECORDS:
        print('%-38s %4d %4d %4d %5d %9.1f'
              % (r['url'].split('//')[-1], r['loc'], r['var'], r['models'],
                 r['days'], r['weighted']))
    print('SUM = %.1f  =>  10000 calls/day 可支撑 %.1f 次；'
          '若每天 20 次，则占用额度 %.0f' % (total, 10000.0 / total if total else 0,
                                             total * 20))
    RECORDS.clear()
    return total


def main():
    lat, lon = DEFAULT_LAT, DEFAULT_LON
    date = (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%d')
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == '--lat' and i + 1 < len(argv):
            lat = float(argv[i + 1])
        elif a == '--lon' and i + 1 < len(argv):
            lon = float(argv[i + 1])
        elif a == '--date' and i + 1 < len(argv):
            date = argv[i + 1]

    mp = _MP()
    _install(mp)
    try:
        print('probe: date=%s point=%.4f,%.4f (no real request is sent)' % (date, lat, lon))
        rpt.build_report(lat, lon, date, 'sunset')
        r1 = _dump('report 霞光报告')

        sig = inspect.signature(rpt.build_alignment)
        kwargs = {}
        for name, p in sig.parameters.items():
            if p.default is inspect.Parameter.empty:
                kwargs[name] = {'lat': lat, 'lon': lon, 'date_str': date}.get(
                    name, 2.0 if name == 'tol' else 10.0)
        rpt.build_alignment(**kwargs)
        r2 = _dump('align 悬日月')

        print('')
        print('预算线：10000 / 20 份 = 500 加权/份。')
        print('TOTAL report=%.1f  align=%.1f' % (r1, r2))
    finally:
        mp.undo()


if __name__ == '__main__':
    main()
