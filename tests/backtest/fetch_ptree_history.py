# -*- coding: utf-8 -*-
"""P-Tree 历史批量采样器：为高保真回测下载日落时刻 B13 亮温
- 每日取"日落 UTC+10min"所在槽位（火烧云典型时段）
- 下载（断点续传+重试）→ 提取光路区域子集 → 存 npz → 删除原文件
- 断点续跑：done 列表存 data/ptree_history_done.txt
用法：
  python fetch_ptree_history.py --limit 3          # 冒烟测试
  python fetch_ptree_history.py                    # 完整窗口（约 9-10 天 FTP 时长）
"""
import argparse
import io
import math
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from ftplib import FTP

import h5py
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.normpath(os.path.join(HERE, '..', '..', 'data',
                                        'ptree_history'))
DONE_FILE = os.path.join(OUT_DIR, 'done.txt')


def _ptree_cfg():
    """凭据优先环境变量 PTREE_HOST/PTREE_USER/PTREE_PASS，回退 app/config_private.json"""
    import json
    host = (os.environ.get('PTREE_HOST') or '').strip()
    user = (os.environ.get('PTREE_USER') or '').strip()
    pw = (os.environ.get('PTREE_PASS') or '').strip()
    if host and user and pw:
        return host, user, pw
    cfg_path = os.path.normpath(os.path.join(HERE, '..', '..', 'app',
                                             'config_private.json'))
    try:
        with open(cfg_path, encoding='utf-8') as f:
            c = json.load(f).get('ptree') or {}
        return c.get('host'), c.get('user'), c.get('pass')
    except Exception:
        return None, None, None
# 上海观测点
LAT, LON = 31.2397, 121.4998
TZ = 8.0
OFFSET_MIN = 10          # 日落 UTC + 10min 所在槽位
WINDOW_START = '2022-06-24'
WINDOW_END = '2023-12-30'
# 光路区域子集（上海 + 西向 600km）
SUB_LAT = (22.0, 36.0)
SUB_LON = (103.0, 123.0)

sys.path.insert(0, HERE)
sys.path.insert(0, os.path.normpath(os.path.join(HERE, '..', '..')))
from app.core import astronomy as astro   # noqa: E402


def connect():
    host, user, pw = _ptree_cfg()
    if not (host and user and pw):
        raise SystemExit('缺少 P-Tree 凭据：设置 PTREE_HOST/PTREE_USER/PTREE_PASS '
                         '或配置 app/config_private.json')
    ftp = FTP(host, timeout=90)
    ftp.login(user, pw)
    ftp.voidcmd('TYPE I')
    return ftp


def remote_size(ftp, remote):
    try:
        return ftp.size(remote)
    except Exception:
        return None


def download_with_resume(ftp, remote, out_path, rsize):
    lsize = os.path.getsize(out_path) if os.path.exists(out_path) else 0
    if lsize == rsize:
        return
    mode = 'ab' if 0 < lsize < rsize else 'wb'
    rest = lsize if 0 < lsize < rsize else None
    with open(out_path, mode) as f:
        ftp.retrbinary('RETR ' + remote, f.write, blocksize=1 << 20,
                       rest=rest)


def extract_subset(nc_path, out_npz):
    f = h5py.File(nc_path, 'r')
    d = f['tbb_13']
    sf = float(np.asarray(d.attrs.get('scale_factor', [1.0])).ravel()[0])
    ao = float(np.asarray(d.attrs.get('add_offset', [0.0])).ravel()[0])
    lat = np.asarray(f['latitude'][:], dtype=np.float32)
    lon = np.asarray(f['longitude'][:], dtype=np.float32)
    arr = np.asarray(d[:], dtype=np.float32) * sf + ao
    f.close()
    arr[arr < 150] = np.nan
    lat_desc = lat[0] > lat[-1]
    if lat_desc:
        j0 = int(np.abs(lat - SUB_LAT[1]).argmin())
        j1 = int(np.abs(lat - SUB_LAT[0]).argmin()) + 1
        sub = arr[j0:j1, :][::-1, :]
        lat_sub = lat[j0:j1][::-1]
    else:
        j0 = int(np.abs(lat - SUB_LAT[0]).argmin())
        j1 = int(np.abs(lat - SUB_LAT[1]).argmin()) + 1
        sub = arr[j0:j1, :]
        lat_sub = lat[j0:j1]
    i0 = int(np.abs(lon - SUB_LON[0]).argmin())
    i1 = int(np.abs(lon - SUB_LON[1]).argmin()) + 1
    sub = sub[:, i0:i1]
    lon_sub = lon[i0:i1]
    np.savez_compressed(out_npz, tbb13=sub.astype(np.float32),
                        lat=lat_sub, lon=lon_sub)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0, help='最多处理 N 天（冒烟）')
    ap.add_argument('--offset-min', type=int, default=OFFSET_MIN)
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    done = set()
    if os.path.exists(DONE_FILE):
        done = set(open(DONE_FILE, encoding='utf-8').read().split())

    d0 = date.fromisoformat(WINDOW_START)
    d1 = date.fromisoformat(WINDOW_END)
    cur = d0
    processed = 0
    ftp = None
    while cur <= d1:
        ds = cur.isoformat()
        day = cur
        cur += timedelta(days=1)
        if ds in done:
            continue
        try:
            st = astro.sun_times(datetime(day.year, day.month, day.day),
                                 LAT, LON, TZ)
        except Exception as e:
            print(ds, 'astro err', str(e)[:60], flush=True)
            continue
        sunset = st.get('sunset')
        if not sunset:
            print(ds, 'no sunset (polar?)', flush=True)
            done.add(ds)
            continue
        sunset_utc = sunset - timedelta(hours=TZ)
        slot_dt = sunset_utc + timedelta(minutes=args.offset_min)
        slot_dt = slot_dt.replace(second=0, microsecond=0)
        d = slot_dt.strftime('%Y%m%d')
        hhmm = slot_dt.strftime('%H%M')
        fn = f'NC_H09_{d}_{hhmm}_R21_FLDK.02801_02401.nc'
        remote = f'jma/netcdf/{d[:6]}/{d[6:]}/{fn}'
        nc_path = os.path.join(OUT_DIR, fn)
        npz_path = os.path.join(OUT_DIR, ds + '_b13.npz')
        try:
            if ftp is None:
                ftp = connect()
            rsize = remote_size(ftp, remote)
            if not rsize:
                print(ds, 'slot missing (skip):', fn, flush=True)
                done.add(ds)
                continue
            print(ds, f'downloading {rsize/1e6:.0f}MB...', flush=True)
            t0 = time.time()
            # 续传循环
            for attempt in range(1, 25):
                lsize = (os.path.getsize(nc_path)
                         if os.path.exists(nc_path) else 0)
                if lsize == rsize:
                    break
                try:
                    download_with_resume(ftp, remote, nc_path, rsize)
                except Exception as e:
                    print('  chunk fail:', str(e)[:60], flush=True)
                    try:
                        ftp.close()
                    except Exception:
                        pass
                    time.sleep(8)
                    ftp = connect()
            if os.path.getsize(nc_path) != rsize:
                print(ds, 'incomplete after retries, will resume next run',
                      flush=True)
                continue
            print(f'  done in {time.time()-t0:.0f}s, extracting...', flush=True)
            extract_subset(nc_path, npz_path)
            os.remove(nc_path)
            done.add(ds)
            with open(DONE_FILE, 'a', encoding='utf-8') as f:
                f.write(ds + '\n')
            processed += 1
            print(ds, 'OK ->', os.path.basename(npz_path), flush=True)
        except Exception as e:
            print(ds, 'ERR', str(e)[:100], flush=True)
            try:
                ftp.close()
            except Exception:
                pass
            ftp = None
            time.sleep(10)
        if args.limit and processed >= args.limit:
            print('limit reached', flush=True)
            break
    try:
        ftp.quit()
    except Exception:
        pass
    print('session finished, processed:', processed, flush=True)


if __name__ == '__main__':
    main()
