# -*- coding: utf-8 -*-
"""模块级静态自检：重复定义 / 语法。

起因：给 satellite.fetch_b13_slot 加了第四个返回值时，新写的
fetch_latest_b13 是"新增"而不是"替换"，旧的同名函数留在下面继续按 3
元组解包 —— 单测全绿（不走网络），线上表现为卫星卡直接显示
"too many values to unpack (expected 3)"。重复的同名顶层定义是这类
编辑事故的通用指纹，这里一次性拦掉。
"""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APP = os.path.join(ROOT, 'app')


def _py_files():
    for base, _dirs, files in os.walk(APP):
        if 'vendor' in base:
            continue
        for fn in files:
            if fn.endswith('.py'):
                yield os.path.join(base, fn)


def _top_level_defs(tree):
    """模块顶层 def / class / 赋值目标名 -> 出现次数。"""
    names = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.append(t.id)
    return names


def test_no_duplicate_top_level_definitions():
    offenders = {}
    for path in _py_files():
        with open(path, encoding='utf-8') as f:
            src = f.read()
        tree = ast.parse(src, filename=path)
        counts = {}
        for n in _top_level_defs(tree):
            counts[n] = counts.get(n, 0) + 1
        dupes = {k: v for k, v in counts.items() if v > 1}
        if dupes:
            offenders[os.path.relpath(path, ROOT)] = dupes
    assert not offenders, f'存在重复的顶层定义：{offenders}'


def test_all_app_modules_parse():
    bad = []
    for path in _py_files():
        try:
            with open(path, encoding='utf-8') as f:
                ast.parse(f.read(), filename=path)
        except SyntaxError as e:
            bad.append(f'{os.path.relpath(path, ROOT)}: {e}')
    assert not bad, bad


def test_satellite_fetch_signatures_consistent():
    """fetch_latest_b13 回 3 元组、fetch_b13_slot 回 4 元组的契约不要被改错。"""
    import inspect

    from app.services import satellite as sat
    src = inspect.getsource(sat.fetch_latest_b13)
    assert 'fetch_b13_slot' in src
    assert 'arr, s, url, _lm = ' in src or '_, _ = ' in src, src
    src2 = inspect.getsource(sat.fetch_b13_slot)
    assert 'return arr, slot_utc, url, lm_dt' in src2
    assert 'return None, None, None, None' in src2
