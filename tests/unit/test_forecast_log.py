# -*- coding: utf-8 -*-
"""预报日志：落盘/回填/统计闭环 + 原子写不留临时文件 + 坏文件隔离。"""
import json

from app.services import forecast_log as flog

REPORT = {
    'meta': {'location': {'lat': 22.27, 'lon': 113.58},
             'date': '2026-09-10', 'type': 'sunset'},
    'astronomy': {'key_time': '18:34', 'key_az': 275.0},
    'probability': 42,
    'verdict': '中烧',
    'credibility': '高 · 多模式高度一致',
    'best_window': {'start': '18:28', 'end': '18:40'},
    'main_model': 'ECMWF',
    'models': {'ECMWF': {'score': 55}},
    'main': {'cloud': {}, 'eq_base_km': 3.5},
}


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(flog, 'LOG_PATH', str(tmp_path / 'forecast_log.json'))


def test_log_prediction_returns_record(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    rec = flog.log_prediction(REPORT)
    assert rec['event_key'] == '2026-09-10_sunset_22.270_113.580'
    assert (tmp_path / 'forecast_log.json').exists()
    assert not list(tmp_path.glob('*.tmp'))      # 原子写：不残留临时文件


def test_outcome_roundtrip_and_stats(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    rec = flog.log_prediction(REPORT)
    assert flog.save_outcome(rec['event_key'], '中烧', '实拍') is True
    assert flog.save_outcome(rec['event_key'], '无效判定') is False
    st = flog.stats()
    assert st['total_scored'] == 1
    assert st['by_verdict']['中烧'] == {'n': 1, 'hits': 1}


def test_save_outcome_unknown_key(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert flog.save_outcome('不存在的key', '不烧') is False


def test_log_and_attach_integration(tmp_path, monkeypatch):
    """report._log_and_attach 应直接用 log_prediction 的返回值，不再二次读盘。"""
    _isolate(tmp_path, monkeypatch)
    from app.services import report as rpt
    bundle = dict(REPORT)
    rpt._log_and_attach(bundle)
    assert bundle['log_key'] == '2026-09-10_sunset_22.270_113.580'
    assert bundle['awaiting_outcome'] in (True, False)
    assert 'outcome' in bundle


def test_corrupt_log_is_quarantined_not_wiped(tmp_path, monkeypatch):
    """坏 JSON 不得静默清空历史：原文件改名隔离，新写入照常。"""
    _isolate(tmp_path, monkeypatch)
    p = tmp_path / 'forecast_log.json'
    p.write_text('{"2026-09-10_sunset_22.270_113.580": {"br', encoding='utf-8')
    rec = flog.log_prediction(REPORT)          # 不得抛异常
    assert rec['event_key'] == '2026-09-10_sunset_22.270_113.580'
    backups = list(tmp_path.glob('forecast_log.json.corrupt-*'))
    assert len(backups) == 1                   # 原件完整留存
    assert backups[0].read_text(encoding='utf-8').startswith('{"2026-09-10_')
    data = json.loads(p.read_text(encoding='utf-8'))
    assert rec['event_key'] in data
    # 再次读写不得反复产生备份
    flog.log_prediction(REPORT)
    flog.stats()
    assert len(list(tmp_path.glob('forecast_log.json.corrupt-*'))) == 1


def test_log_root_not_object_is_quarantined(tmp_path, monkeypatch):
    """根节点类型不对（列表）同样按坏文件隔离，不覆盖。"""
    _isolate(tmp_path, monkeypatch)
    p = tmp_path / 'forecast_log.json'
    p.write_text('[1, 2, 3]', encoding='utf-8')
    assert flog._load() == {}
    assert len(list(tmp_path.glob('forecast_log.json.corrupt-*'))) == 1


def test_empty_or_missing_log_is_not_corrupt(tmp_path, monkeypatch):
    """首次运行（无文件）或 0 字节文件不算损坏，不该产生备份。"""
    _isolate(tmp_path, monkeypatch)
    assert flog._load() == {}
    (tmp_path / 'forecast_log.json').write_text('', encoding='utf-8')
    assert flog._load() == {}
    assert not list(tmp_path.glob('forecast_log.json.corrupt-*'))
