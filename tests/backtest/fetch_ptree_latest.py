# -*- coding: utf-8 -*-
"""下载/续传 P-Tree 最新 5km 全圆盘 NetCDF
每次尝试新建连接 + REST 断点续传 + 多轮重试（应对 VPN 链路停滞）"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from ftplib import FTP

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.normpath(os.path.join(HERE, '..', '..', 'data', 'sat_cache'))


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


def connect():
    host, user, pw = _ptree_cfg()
    if not (host and user and pw):
        raise SystemExit('缺少 P-Tree 凭据：设置 PTREE_HOST/PTREE_USER/PTREE_PASS '
                         '或配置 app/config_private.json')
    ftp = FTP(host, timeout=60)
    ftp.login(user, pw)
    ftp.voidcmd('TYPE I')
    return ftp


def remote_size(ftp, remote):
    try:
        return ftp.size(remote)
    except Exception:
        return None


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    now = datetime.now(timezone.utc)
    slots = []
    grid = now.replace(minute=(now.minute // 10) * 10, second=0,
                       microsecond=0)
    for k in range(0, 4):
        slot = grid - timedelta(minutes=10 * k)
        d = slot.strftime('%Y%m%d')
        hhmm = slot.strftime('%H%M')
        slots.append((slot, d, hhmm))

    target = None
    # 找到最新存在的槽位
    for slot, d, hhmm in slots:
        fn = f'NC_H09_{d}_{hhmm}_R21_FLDK.02801_02401.nc'
        remote = f'jma/netcdf/{d[:6]}/{d[6:]}/{fn}'
        try:
            ftp = connect()
            rs = remote_size(ftp, remote)
            ftp.close()
        except Exception as e:
            print('connect fail:', str(e)[:80], flush=True)
            time.sleep(5)
            continue
        if rs:
            target = (slot, d, hhmm, fn, remote, rs)
            break
        print('no remote:', fn, flush=True)

    if not target:
        print('NO SLOT AVAILABLE', flush=True)
        sys.exit(1)
    slot, d, hhmm, fn, remote, rsize = target
    out = os.path.join(OUT_DIR, fn)
    print(f'target {fn} remote={rsize/1e6:.1f}MB', flush=True)

    for attempt in range(1, 15):
        lsize = os.path.getsize(out) if os.path.exists(out) else 0
        if lsize == rsize:
            print(f'COMPLETE {fn} {lsize/1e6:.1f}MB '
                  f'(attempts={attempt})', flush=True)
            return
        try:
            ftp = connect()
            with open(out, 'ab' if lsize else 'wb') as f:
                ftp.retrbinary('RETR ' + remote, f.write, blocksize=1 << 20,
                               rest=lsize if lsize else None)
            ftp.close()
        except Exception as e:
            print(f'attempt {attempt}: {str(e)[:80]}', flush=True)
            try:
                ftp.close()
            except Exception:
                pass
            time.sleep(5)
    print('FAILED after retries', flush=True)
    sys.exit(1)


if __name__ == '__main__':
    main()
