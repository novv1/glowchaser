# -*- coding: utf-8 -*-
"""前瞻性验证日志：预报落盘 + 实况回填 + 命中统计
数据文件：data/forecast_log.json  结构 {event_key: {...}}
event_key = f'{date}_{type}_{lat:.3f}_{lon:.3f}'
每次查询 upsert（保留最早预报时间 + 最近一次预报快照）；事件后可回填 outcome。
"""
import json
import logging
import os
import tempfile
import threading
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.normpath(os.path.join(_HERE, '..', '..', 'data',
                                         'forecast_log.json'))
_LOCK = threading.Lock()
log = logging.getLogger(__name__)

VERDICTS = ['不烧', '微烧', '小烧', '中烧', '大烧']


def _quarantine(reason):
    """坏文件改名隔离：只备份、绝不覆盖或删除。

    历史预测是校准曲线的唯一证据，解析失败时退回空表继续服务，
    原文件留成 ``forecast_log.json.corrupt-<时间戳>`` 供事后取证。
    """
    try:
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        dst = f'{LOG_PATH}.corrupt-{stamp}'
        n = 1
        while os.path.exists(dst):
            n += 1
            dst = f'{LOG_PATH}.corrupt-{stamp}-{n}'
        os.replace(LOG_PATH, dst)
        log.warning('[forecast_log] 日志解析失败（%s），已隔离为 %s',
                    reason, os.path.basename(dst))
    except Exception:
        log.warning('[forecast_log] 隔离损坏日志失败', exc_info=True)


def _load():
    try:
        with open(LOG_PATH, encoding='utf-8') as f:
            raw = f.read()
    except FileNotFoundError:
        return {}                     # 首次运行：还没有日志文件
    except OSError as e:
        log.warning('[forecast_log] 读取失败：%s', e)
        return {}
    if not raw.strip():
        return {}                     # 0 字节文件不算损坏
    try:
        data = json.loads(raw)
    except Exception as e:
        _quarantine(e)
        return {}
    if not isinstance(data, dict):
        _quarantine(f'根节点是 {type(data).__name__}，不是对象')
        return {}
    return data


def _save(data):
    """同目录唯一临时文件 + fsync + 原子 replace：多进程并发也不会写坏主文件。"""
    d = os.path.dirname(LOG_PATH)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.forecast_log_', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, LOG_PATH)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def log_prediction(report: dict):
    """upsert 预测记录（事件后不再覆盖预测快照）"""
    meta = report.get('meta', {})
    astro = report.get('astronomy', {})
    lat = meta.get('location', {}).get('lat')
    lon = meta.get('location', {}).get('lon')
    key = f"{meta.get('date')}_{meta.get('type')}_{lat:.3f}_{lon:.3f}"
    main = report.get('main', {})
    with _LOCK:
        data = _load()
        rec = data.get(key, {
            'event_key': key,
            'first_logged': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'outcome': None,
        })
        rec.update({
            'date': meta.get('date'),
            'type': meta.get('type'),
            'lat': lat, 'lon': lon,
            'location_name': (report.get('nowcast') or {}).get('label', ''),
            'event_time': astro.get('key_time'),
            'event_az': astro.get('key_az'),
            'last_logged': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'prediction': {
                'probability': report.get('probability'),
                'verdict': report.get('verdict'),
                'credibility': report.get('credibility'),
                'best_window': report.get('best_window'),
                'main_model': report.get('main_model'),
                'model_scores': {k: v.get('score')
                                 for k, v in (report.get('models') or {}).items()},
                'cloud': main.get('cloud'),
                'eq_base_km': main.get('eq_base_km'),
                'boundary_km': main.get('boundary_km'),
                'quality': main.get('quality'),
                't_start_min': main.get('t_start_min'),
                't_end_min': main.get('t_end_min'),
            },
            'ens_spread': (report.get('credibility') or ''),
        })
        # 事件已过则冻结预测快照（等待回填）
        try:
            ev_dt = datetime.strptime(
                f"{meta.get('date')} {astro.get('key_time')}", '%Y-%m-%d %H:%M')
            rec['awaiting_outcome'] = ev_dt < datetime.now()
        except Exception:
            rec['awaiting_outcome'] = False
        data[key] = rec
        _save(data)
    return rec


def save_outcome(event_key: str, outcome: str, note: str = ''):
    if outcome not in VERDICTS:
        return False
    with _LOCK:
        data = _load()
        if event_key not in data:
            return False
        data[event_key]['outcome'] = outcome
        data[event_key]['outcome_note'] = note
        data[event_key]['outcome_at'] = datetime.now().strftime('%Y-%m-%d %H:%M')
        data[event_key]['awaiting_outcome'] = False
        _save(data)
    return True


def pending_keys():
    """事件已过且未回填的记录"""
    data = _load()
    return [k for k, v in data.items() if v.get('awaiting_outcome')]


def stats():
    """按预报判定分桶的实际命中率（供校准曲线迭代）"""
    data = _load()
    buckets = {}
    for v in data.values():
        o = v.get('outcome')
        if not o:
            continue
        p = v.get('prediction', {})
        verdict = p.get('verdict')
        b = buckets.setdefault(verdict, {'n': 0, 'hits': 0})
        b['n'] += 1
        if o != '不烧':
            b['hits'] += 1
    total = sum(b['n'] for b in buckets.values())
    return {'total_scored': total, 'by_verdict': buckets}
