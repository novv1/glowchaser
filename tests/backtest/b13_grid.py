# -*- coding: utf-8 -*-
"""向日葵 B13 网格产品拉取：只留珠三角光路窗口。

数据源：JAXA P-Tree FTP `/jma/netcdf/{YYYYMM}/{DD}/`
        ``NC_H08|H09_{YYYYMMDD}_{HHMM}_R21_FLDK.02401_02401.nc``
（JAXA Himawari L1 Gridded Data；EQR 投影 60S-60N / 70E-150W；
10 分钟一帧；0.05° 网格 2401×2401；约 97 MB/帧。文件内含
``tbb_07``~``tbb_16`` 亮温、``albedo_01``~``06``、以及 ``SOZ``/``SOA``
太阳天顶角与方位角——太阳几何不用自己算。）

为什么不用别的源（2026-09 实测，别再踩一遍）
------------------------------------------------
- **JAXA CLP 云特性 L2**（0.5 MB/帧，本该最合适）：云特性反演依赖可见光，
  日落前后整片缺测——实测珠三角 19:10/19:40 CST 的 CLP 100% 无效
  （QA=1）。霞恰恰发生在那个时段，**出局**。
- **AWS ``noaa-himawari8`` 的 ``AHI-L2-FLDK-Clouds``**（云顶高度 CHGT）：
  单文件 597 MB，且 h5py 走 S3 范围读要反复拉散落的元数据，实测 3 分钟
  仍未打开。**出局**。
- **P-Tree 的 ``r14`` 产品**：是日本区域（24-50N / 123-150E），
  **不含珠三角**；全盘只有 5 km（97 MB）与 2 km（483 MB）两档。
- **FTP 随机读**：单次 REST 取 1 MB 花 96 秒（小传输的 TCP 慢启动），
  整文件顺序下载反而更快。所以只能整帧下载后裁剪。
- **AWS 分块 B13**（``AHI-L1b-FLDK`` 的 ``_B13_FLDK_R20_Sxxxx.DAT.bz2``，
  每块 1~3 MB、全盘 22 MB、免 key）：体量只有 1/30，是更优的路。已实测
  解压后正好 5500×550×2 字节——**数据块是未压缩的 uint16**，不需要 satpy。
  但它还要 HSD 头里的导航（CFAC/LFAC/COFF/LOFF）与标定系数，本机拿不到
  参考实现（GitHub 不可达），逆向未完成，**暂未接入**，见文件末尾 TODO。

单连接被服务端限速，但**并发几乎线性扩展**（实测 1/2/4/8 连接 =
110/221/485/828 KB/s），所以用线程池多连接并行，8 路下一帧约 2 分钟。

用法（冒烟 3 天）::

    python tests/backtest/b13_grid.py --start 2024-06-01 --limit 3 --jobs 8

窗口数据落 ``data/b13_grid/*.npz``，约 0.2 MB/帧；原文件下完即删。

TODO: 接入 AWS 分块（省 30 倍体量，但要先补 HSD 导航解析）。
"""
import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from ftplib import FTP

import h5py
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, 'data', 'b13_grid')

# 抽取窗口：珠三角 + 西向光路最远约 700 km + 反日一侧余量
WIN = {'lat': (18.0, 28.0), 'lon': (105.0, 122.0)}

# 只抽这 3 个变量：B13 亮温 + 太阳天顶角/方位角
VARS = ('tbb_13', 'SOZ', 'SOA')

_NC = re.compile(r'^NC_H\d\d_(\d{8})_(\d{4})_R21_FLDK\.(\d{5})_(\d{5})\.nc$')


def _ptree_cfg():
    """凭据优先环境变量 PTREE_HOST/PTREE_USER/PTREE_PASS，回退
    app/config_private.json。与 tests/backtest/fetch_ptree_history.py 同口径。"""
    host = (os.environ.get('PTREE_HOST') or '').strip()
    user = (os.environ.get('PTREE_USER') or '').strip()
    pw = (os.environ.get('PTREE_PASS') or '').strip()
    if host and user and pw:
        return host, user, pw
    cfg_path = os.path.join(ROOT, 'app', 'config_private.json')
    try:
        with open(cfg_path, encoding='utf-8') as f:
            c = json.load(f).get('ptree') or {}
        return c.get('host'), c.get('user'), c.get('pass')
    except Exception:
        return None, None, None


def connect():
    host, user, pw = _ptree_cfg()
    if not (host and user and pw):
        raise SystemExit('缺少 P-Tree 凭据：设置 PTREE_HOST/PTREE_USER/PTREE_PASS '
                         '或配置 app/config_private.json')
    ftp = FTP(host, timeout=120)
    ftp.login(user, pw)
    ftp.voidcmd('TYPE I')
    return ftp


def day_slots(ftp, day, _cache={}):
    """列出某日所有可用槽位 -> {'HHMM': (远程文件名, 字节数)}。

    目录列一次即缓存（同一天多次取槽只列一次）。文件名前缀 H08/H09
    不写死：向日葵 9 号 2022-12-13 起接替，列目录按实际名字取。

    网格产品名里的 ``02801_02401`` 是「行_列」：2026 起经度轴东扩到
    210°E，全圆盘从 2401×2401 变 2401×2801（纬度轴与 0.05° 间距都没变，
    extract_window 按经纬度范围裁剪，无需跟着改）。同槽位可能同时挂着
    ``07001_06001``（0.02° 高分辨率，550MB+/帧）——我们按列数选
    **02401 列**的产品（0.05°，约 115MB），高分辨率那档没有对应的轻量替代。
    """
    key = day.isoformat()
    if key in _cache:
        return _cache[key]
    remote_dir = 'jma/netcdf/%s/%s' % (day.strftime('%Y%m'), day.strftime('%d'))
    try:
        entries = list(ftp.mlsd(remote_dir))
    except Exception as e:
        # 不缓存失败结果：曾经把列目录异常也写进缓存，于是那一天永远排不出
        # 任务（表现为「3 天只拉了 1 帧」），而且不会自愈。
        print('  列目录失败（不缓存，下次重试）%s: %s' % (day, str(e)[:60]),
              flush=True)
        return {}
    found = {}
    for name, facts in entries:
        m = _NC.match(name)
        if m and m.group(1) == day.strftime('%Y%m%d'):
            sz = facts.get('size')
            found.setdefault(m.group(2), []).append(
                ('%s/%s' % (remote_dir, name), int(sz) if sz else None,
                 m.group(4)))
    out = {}
    for hhmm, cands in found.items():
        # 优先 02401 列（0.05° 档）；同槽位多产品时再取字节数最小的
        cands.sort(key=lambda c: (c[2] != '02401', c[1] or 1 << 60))
        out[hhmm] = (cands[0][0], cands[0][1])
    _cache[key] = out
    return out


def extract_window(nc_path, win=None):
    """从整帧 netCDF 抽出窗口，返回 dict（含经纬度轴）。

    缺测（missing_value / 超出 valid 范围）填 np.nan。
    """
    win = win or WIN
    out = {}
    with h5py.File(nc_path, 'r') as f:
        lat = np.asarray(f['latitude'][:], dtype=np.float64)
        lon = np.asarray(f['longitude'][:], dtype=np.float64)
        # latitude 自北向南（60 -> -60），lon 0..360；PRD 在东经
        lat_lo, lat_hi = win['lat']
        lon_lo, lon_hi = win['lon']
        j = np.where((lat >= lat_lo) & (lat <= lat_hi))[0]
        i = np.where((lon >= lon_lo) & (lon <= lon_hi))[0]
        if not len(j) or not len(i):
            raise ValueError('窗口与网格不重叠：lat %s lon %s' % (win['lat'], win['lon']))
        j0, j1 = int(j[0]), int(j[-1]) + 1
        i0, i1 = int(i[0]), int(i[-1]) + 1
        for v in VARS:
            d = f[v]
            sf = float(np.asarray(d.attrs.get('scale_factor', [1.0])).ravel()[0])
            ao = float(np.asarray(d.attrs.get('add_offset', [0.0])).ravel()[0])
            mv = d.attrs.get('missing_value')
            arr = np.asarray(d[j0:j1, i0:i1], dtype=np.float32) * sf + ao
            if mv is not None:
                mv = float(np.asarray(mv).ravel()[0]) * sf + ao
                arr[np.isclose(arr, mv, atol=1e-3)] = np.nan
            if v == 'tbb_13':
                # P-Tree 的 tbb_* 是**开尔文**（实测分位 197.6~295.9 K）。
                # 在这里一次性转成摄氏度：下游的「比地面暖 -> 晴空」判据、
                # 云顶高度反演全都按摄氏度写，混着单位会静默把整片天空判成
                # 晴空（曾经就是这样把有云日全判成无云）。
                arr = arr - 273.15
            out[v] = arr
        out['lat'] = lat[j0:j1].astype(np.float32)
        out['lon'] = lon[i0:i1].astype(np.float32)
        out['tbb_unit'] = np.array('degC')   # 标记单位，防止旧缓存被误解
    return out


def window_key(day, hhmm):
    return '%s_%s' % (day.strftime('%Y%m%d'), hhmm)


def _pid_alive(pid):
    """进程是否还在（跨平台，尽力而为）。"""
    if pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_lock(out_dir):
    """单实例锁，返回锁文件路径。

    两个进程写同一个 OUT_DIR 会把断点续传的 ``.part`` 追加错位（实测：
    上一个进程没被 pkill 干净，同一文件本地涨到 98 MB 而远端只有 96 MB）。
    中断运行留下的陈旧锁（pid 已不在）会被自动清理。
    """
    os.makedirs(out_dir, exist_ok=True)
    lock = os.path.join(out_dir, '.lock')
    if os.path.exists(lock):
        try:
            with open(lock, encoding='utf-8') as f:
                old = int(f.read().strip() or 0)
        except Exception:
            old = 0
        if _pid_alive(old):
            raise SystemExit('已有实例在跑（pid %d，锁 %s）。并发写同一目录会'
                             '污染 .part —— 先停掉它再跑。' % (old, lock))
        print('清理陈旧锁（pid %d 已不在）' % old, flush=True)
        try:
            os.remove(lock)
        except OSError:
            pass
    with open(lock, 'w', encoding='utf-8') as f:
        f.write(str(os.getpid()))
    return lock


_local = threading.local()


def _conn():
    """每线程一条 FTP 连接。并发是这里唯一的提速手段（见模块说明）。"""
    ftp = getattr(_local, 'ftp', None)
    if ftp is None:
        ftp = connect()
        _local.ftp = ftp
    return ftp


def _reconnect():
    """丢弃本线程连接并重连（下载失败后调用）。"""
    try:
        _local.ftp.close()
    except Exception:
        pass
    _local.ftp = None
    for _ in range(2):
        try:
            return _conn()
        except Exception:
            time.sleep(10)
    return None


def download_resume(ftp, remote, out_path, rsize, max_attempts=20):
    """断点续传下载整帧，返回 (ftp, 是否下完)。

    P-Tree 传 90+ MB 中途会掉线，必须续传：``rest=`` 从已落盘字节继续，
    每次失败重连一次再试。与 tests/backtest/fetch_ptree_history.py 同口径。

    ``rsize`` 为 None 时**直接放弃**而不是重下——长度未知就无法判断是否
    下完，曾经因此把已下到的 94 MB 截断重来（见 ``day_slots`` 的说明）。

    已落盘字节**超过** rsize 说明文件被两个进程同时追加过（实测：上一个
    进程没被 pkill 干净，两个实例往同一 .part 写，本地 98 MB > 远端
    96 MB），此时内容已错位，只能删掉重下。
    """
    if not rsize:
        return ftp, False
    for _ in range(max_attempts):
        lsize = os.path.getsize(out_path) if os.path.exists(out_path) else 0
        if lsize == rsize:
            return ftp, True
        if lsize > rsize:
            print('   已落盘 %.1fMB > 远端 %.1fMB，判定污染，重下'
                  % (lsize / 1e6, rsize / 1e6), flush=True)
            try:
                os.remove(out_path)
            except OSError:
                pass
            lsize = 0
        rest = lsize if 0 < lsize < rsize else None
        mode = 'ab' if rest else 'wb'
        try:
            with open(out_path, mode) as f:
                ftp.retrbinary('RETR ' + remote, f.write,
                               blocksize=1 << 20, rest=rest)
        except Exception as e:
            print('   续传重连（%s）' % str(e)[:60], flush=True)
            time.sleep(3)
            ftp = _reconnect()
            if ftp is None:
                return None, False
    return ftp, False


def fetch_window(day, hhmm, remote, rsize, win=None, out_dir=None):
    """下载某槽整帧、抽窗口、落 npz -> (npz 路径, 是否新下载)。

    用调用线程自己的连接。原文件抽完即删（94 MB 不留）；npz 已存在则直接
    返回；没下完则保留 ``.nc.part`` 供下次续传。
    """
    out_dir = out_dir or OUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    npz = os.path.join(out_dir, window_key(day, hhmm) + '.npz')
    if os.path.exists(npz):
        return npz, False
    part = npz + '.nc.part'
    _, ok = download_resume(_conn(), remote, part, rsize)
    if not ok:
        return None, False                # 没下完，保留 .part 下次续
    with open(npz + '.part', 'wb') as f:  # 原子落盘：先写临时文件
        np.savez_compressed(f, **extract_window(part, win=win))
    os.replace(npz + '.part', npz)
    os.remove(part)
    return npz, True


def read_window(npz):
    with np.load(npz) as z:
        return {k: z[k] for k in z.files}


def slot_time(event_local, tz_offset_h, offset_min):
    """事件时刻 + 偏移 -> (对齐到整 10 分钟的本地时刻, UTC 日期, 槽位 'HHMM')。

    槽位必须向下取整：卫星是 10 分钟一帧（00/10/20...），日落 19:11 加
    30 分钟是 19:41，对应的是 19:40 那一帧。拉取和打标两边都走这个函数，
    否则年份一样、槽位差一分钟，就会「明明下过却读不到」。

    第二位是槽位所在的 **UTC 日期**：朝霞在本地清晨，对应**前一天**的
    UTC 傍晚帧（06:02 CST = 前一日 22:02 UTC）。曾经只返回 HHMM、日期
    沿用本地日，远端目录与 window_key 两边都差一天，表现为 AM 永远
    「无数据」——PM 恰好同日，只拿晚霞冒烟测不出来。
    """
    t = event_local + timedelta(minutes=offset_min)
    t = t.replace(minute=(t.minute // 10) * 10, second=0, microsecond=0)
    utc_dt = t - timedelta(hours=tz_offset_h)
    return t, utc_dt.date(), utc_dt.strftime('%H%M')


def _slots_around(event_local, tz_offset_h, offsets_min):
    """事件时刻前后的一组 (UTC 日期, 槽位 'HHMM')（整 10 分钟对齐）。"""
    return [slot_time(event_local, tz_offset_h, off)[1:]
            for off in offsets_min]


def main():
    ap = argparse.ArgumentParser(description='拉取向日葵 B13 珠三角窗口')
    ap.add_argument('--start', required=True, help='起始日 YYYY-MM-DD')
    ap.add_argument('--end', help='结束日 YYYY-MM-DD（含）')
    ap.add_argument('--limit', type=int, default=0, help='最多处理 N 天（冒烟）')
    ap.add_argument('--lat', type=float, default=23.1291, help='观测点纬度')
    ap.add_argument('--lon', type=float, default=113.2644, help='观测点经度')
    ap.add_argument('--tz', type=float, default=8.0, help='时区偏移（小时）')
    ap.add_argument('--offsets', default='-10,10,30',
                    help='相对日落/日出的分钟偏移，逗号分隔')
    ap.add_argument('--period', default='PM', choices=['AM', 'PM', 'both'])
    ap.add_argument('--jobs', type=int, default=6,
                    help='并发连接数。单连接被服务端限速（实测 110 KB/s），'
                         '而并发几乎线性扩展（8 路 828 KB/s）')
    args = ap.parse_args()

    from app.core import astronomy as astro

    offsets = [int(x) for x in args.offsets.split(',') if x.strip()]
    d0 = date.fromisoformat(args.start)
    d1 = date.fromisoformat(args.end) if args.end else d0
    periods = ['AM', 'PM'] if args.period == 'both' else [args.period]

    lock = acquire_lock(OUT_DIR)
    try:
        _run(args, astro, offsets, d0, d1, periods)
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def _run(args, astro, offsets, d0, d1, periods):
    # 主线程先把每天要拉的槽位列出来（列目录不并发，避免连接共享问题）
    tasks = []
    cur = d0
    done_days = 0
    ftp = connect()
    while cur <= d1:
        if args.limit and done_days >= args.limit:
            break
        done_days += 1
        st = astro.sun_times(datetime(cur.year, cur.month, cur.day),
                             args.lat, args.lon, args.tz)
        for period in periods:
            ev = st.get('sunset' if period == 'PM' else 'sunrise')
            if not ev:
                continue
            for utc_day, hhmm in _slots_around(ev, args.tz, offsets):
                # 朝霞的帧挂在前一天的 UTC 目录下，day_slots 按日缓存
                slots = day_slots(ftp, utc_day)
                if hhmm in slots:
                    remote, rsize = slots[hhmm]
                    tasks.append((utc_day, period, hhmm, remote, rsize))
        cur += timedelta(days=1)

    if not tasks:
        raise SystemExit('没有可拉的槽位（检查日期范围与凭据）')
    print('待拉 %d 帧，并发 %d' % (len(tasks), args.jobs), flush=True)

    n_new = n_skip = n_miss = 0
    lock = threading.Lock()
    t_start = time.time()

    def run(task):
        nonlocal n_new, n_skip, n_miss
        day, period, hhmm, remote, rsize = task
        t0 = time.time()
        npz, is_new = fetch_window(day, hhmm, remote, rsize)
        with lock:
            if npz is None:
                n_miss += 1
                print('%s %s %s  未下完（保留 .part 续传）' % (day, period, hhmm),
                      flush=True)
            elif is_new:
                n_new += 1
                print('%s %s %s  OK %.1fMB %.0fs' % (
                    day, period, hhmm, os.path.getsize(npz) / 1e6,
                    time.time() - t0), flush=True)
            else:
                n_skip += 1

    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        list(ex.map(run, tasks))

    print('新下载 %d 帧，跳过(已缓存) %d，未完成 %d，共 %.1f 分钟'
          % (n_new, n_skip, n_miss, (time.time() - t_start) / 60), flush=True)


if __name__ == '__main__':
    main()
