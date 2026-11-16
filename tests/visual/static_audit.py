# -*- coding: utf-8 -*-
"""静态审计：跨文件找"会静默失效"的那类 bug。

这一类问题的共同点是**不报错**：接口照常请求、代码照常执行，只是用户永远看不到
（v2.28 就栽在这——地平线热力图卡的 `#horizonCard` 全项目没人打开，功能等于不存在）。
本脚本检查：
  1. JS 引用了 HTML 里不存在的 id（点不动 / 拿不到值 / 静默 return）
  2. HTML 里存在但全项目没人引用的 id（候选"死 UI"）
  3. HTML 里重复的 id（getElementById 只会拿到第一个）
  4. JS 调用但没有任何文件定义的函数（拼错名 / 重构漏改）
  5. 前端 fetch 的接口在 FastAPI 里没有对应路由（404）
  6. UI_VERSION / APP_VERSION / 静态资源 ?v=NN 三者是否自洽
用法：python tests/visual/static_audit.py        # 有则列出，退出码 1
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATIC = os.path.join(ROOT, 'app', 'static')

# 浏览器/库内置，不算"未定义"
BUILTIN = {
    'if', 'for', 'while', 'switch', 'catch', 'return', 'typeof', 'function',
    'fetch', 'parseInt', 'parseFloat', 'isNaN', 'isFinite', 'setTimeout',
    'clearTimeout', 'setInterval', 'clearInterval', 'requestAnimationFrame',
    'cancelAnimationFrame', 'alert', 'confirm', 'prompt', 'encodeURIComponent',
    'decodeURIComponent', 'btoa', 'atob', 'structuredClone', 'queueMicrotask',
    'Number', 'String', 'Boolean', 'Array', 'Object', 'Math', 'JSON', 'Date',
    'Map', 'Set', 'Promise', 'RegExp', 'Error', 'Image', 'Event', 'URL',
    'URLSearchParams', 'AbortController', 'Intl', 'L', 'echarts', 'leaflet',
    'require', 'import', 'super', 'this', 'new', 'delete', 'void', 'in', 'of',
    'do', 'else', 'try', 'finally', 'throw', 'class', 'extends', 'await',
    'async', 'yield', 'await', 'get', 'set', 'static',
    'getComputedStyle', 'bezier', 'translateX', 'matrix', 'gradient',
}


def read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def main():
    html = read(os.path.join(STATIC, 'index.html'))
    js_files = [f for f in sorted(os.listdir(STATIC)) if f.endswith('.js')]
    js = {f: read(os.path.join(STATIC, f)) for f in js_files}
    all_js = '\n'.join(js.values())
    css = read(os.path.join(STATIC, 'style.css'))
    # CSS 里以 #id 选择器出现的也算"在用"（很多元素只做样式、无 JS 引用）
    css_ids = set(re.findall(r'#([A-Za-z][A-Za-z0-9_\-]*)', css))
    # 'xxx' + 'Ctrl' 这类拼接（卫星时间轴的 nowcastCtrl / alignNcCtrl 走这条路）
    suffix = set(re.findall(r"[+\s]'([A-Z][A-Za-z0-9_]{2,})'", all_js))

    html_ids = re.findall(r'\bid="([^"]+)"', html)
    id_set = set(html_ids)
    problems = []

    # 1) 重复 id
    seen, dup = set(), []
    for i in html_ids:
        if i in seen and i not in dup:
            dup.append(i)
        seen.add(i)
    if dup:
        problems.append(('重复 id', dup))

    # 2) JS 引用了不存在的 id
    #    排除两类合法写法：动态生成的 id（innerHTML 里写的、或 'pane-' + tab
    #    这种前缀拼接）——它们在 HTML 里本来就不该出现。
    quoted_ids = set(re.findall(r"['\"]([A-Za-z][A-Za-z0-9_\-]*)['\"]", all_js))
    used_ids = set()
    missing = {}
    for f, src in js.items():
        for pat in (r"getElementById\(\s*'([^']+)'", r'getElementById\(\s*"([^"]+)"',
                    r"querySelector\(\s*'#([A-Za-z0-9_\-]+)",
                    r'querySelector\(\s*"#([A-Za-z0-9_\-]+)'):
            for m in re.finditer(pat, src):
                name = m.group(1)
                used_ids.add(name)
                if name in id_set:
                    continue
                if name in quoted_ids:          # 运行时生成的元素
                    continue
                if name.endswith('-') or '+' in name:   # 'pane-' + tab
                    continue
                missing.setdefault(f, set()).add(name)
    if missing:
        problems.append(('JS 引用了不存在的 id',
                         {k: sorted(v) for k, v in missing.items()}))

    # 3) HTML 里存在但没人引用的 id（候选死 UI；只提示不判错）
    #    动态生成的 id（模板字符串里写的）也算被引用
    dyn = set(re.findall(r'id="([A-Za-z0-9_\-]+)"', all_js)) \
        | set(re.findall(r"id='([A-Za-z0-9_\-]+)'", all_js))
    unused = sorted(i for i in id_set
                    if i not in used_ids and i not in dyn
                    and i not in quoted_ids and i not in css_ids
                    and not any(i.endswith(sfx) for sfx in suffix)
                    and not i.startswith('pane-'))
    notes = []
    if unused:
        # 只作提示：大量 id 是静态文案 / 仅 CSS 选择器用 / 布局容器，属正常
        notes.append(('全项目都没引用的 id（静态文案或仅 CSS 使用，人工扫一眼即可）',
                      unused))

    # 3b) 默认 display:none 的卡片：JS 有没有把它打开过（v2.28 的"死功能"就是这类）
    hidden = re.findall(r'id="([A-Za-z0-9_\-]+)"[^>]*style="[^"]*display\s*:\s*none',
                        html)
    never = []
    for hid in hidden:
        hit = False
        for src in js.values():
            for m in re.finditer(re.escape(hid), src):
                seg = src[max(0, m.start() - 900):m.start() + 900]
                if 'display' in seg:
                    hit = True
                    break
            if hit:
                break
        if not hit and any(hid.endswith(sfx) for sfx in suffix):
            hit = True          # 拼接式访问（key + 'Ctrl'）也算有人管
        if not hit:
            never.append(hid)
    if never:
        problems.append(('HTML 里 display:none 但 JS 从没设置过 display（可能永远看不见）',
                         never))

    # 4) 调用但未定义的函数：只留"全局函数名"（无 . 前缀、非局部同名）里
    #    出现 ≥2 次的，噪声大但偶尔能抓到拼错的重构残留
    defined = set(re.findall(r'function\s+([A-Za-z_][A-Za-z0-9_]*)', all_js))
    defined |= set(re.findall(r'(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=',
                              all_js))
    called = {}
    for m in re.finditer(r'(?<![\w.$])([a-z][A-Za-z0-9_]{3,})\s*\(', all_js):
        called[m.group(1)] = called.get(m.group(1), 0) + 1
    undef = sorted(c for c, n in called.items()
                   if c not in defined and c not in BUILTIN and c not in id_set
                   and n >= 2 and c not in quoted_ids)
    if undef:
        problems.append(('出现≥2次的未定义函数名（请人工确认是否拼错）', undef))

    # 5) 前端 fetch 的接口在后端路由里找不到
    main_py = read(os.path.join(ROOT, 'app', 'main.py'))
    routes = set(re.findall(r'@app\.(?:get|post)\(\s*[\'"]([^\'"]+)', main_py))
    fetched = set(re.findall(r"[`'\"]\$\{API\}(/api/[A-Za-z0-9_\-/{}$]+)", all_js))
    fetched |= set(re.findall(r"API\s*\+\s*'(/api/[A-Za-z0-9_\-/]+)'", all_js))
    norm = set()
    for r in routes:
        norm.add(re.sub(r'\{[^}]+\}', 'X', r))
    bad = []
    for f in fetched:
        f2 = re.sub(r'\$\{[^}]+\}', 'X', f).split('?')[0]
        if f2 not in norm and not any(f2.startswith(n) for n in norm):
            bad.append(f)
    if bad:
        problems.append(('前端调用的接口没有对应路由', sorted(set(bad))))

    # 6) 版本自洽
    ui = re.search(r"UI_VERSION\s*=\s*'([^']+)'", all_js)
    app_v = re.search(r"APP_VERSION\s*=\s*'([^']+)'",
                      read(os.path.join(ROOT, 'app', 'services', 'report.py')))
    vers = set(re.findall(r'\?v=(\d+)', html))
    ver_bad = []
    if ui and app_v and ui.group(1) != app_v.group(1):
        ver_bad.append(f'UI_VERSION {ui.group(1)} != APP_VERSION {app_v.group(1)}')
    if len(vers) > 1:
        ver_bad.append(f'index.html 里 ?v= 不一致：{sorted(vers)}')
    if ver_bad:
        problems.append(('版本号不自洽', ver_bad))

    if not problems:
        print('静态审计：未发现问题')
        return 0
    print('静态审计发现 %d 类问题：\n' % len(problems))
    for name, detail in problems:
        print(f'── {name}')
        if isinstance(detail, dict):
            for k, v in detail.items():
                print(f'   {k}: {v}')
        else:
            for v in detail:
                print(f'   {v}')
        print()
    return 1


if __name__ == '__main__':
    sys.exit(main())
