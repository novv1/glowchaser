# -*- coding: utf-8 -*-
"""下载 VIIRS 光污染栅格（World Atlas / VIIRS DNB 中国区域裁剪 tif）
→ data/lightpollution.tif 供 terrain.light_pollution 采样。
多源依次尝试；全部失败时 terrain 自动用启发式估算（不阻塞功能）。
也可手动下载后放置：GeoTIFF，需含 ModelPixelScale/ModelTiepoint tag，
值域为辐射亮度 nW/cm²/sr（World Atlas 2015 口径）。
运行：python tests/backtest/fetch_lightpollution.py
"""
import os
import sys

import requests

OUT = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..', 'data',
    'lightpollution.tif'))

# 候选源（公开镜像，逐个尝试；均失败则打印手动放置指引）
SOURCES = [
    # GitHub 开源镜像（World Atlas 2015 中国区裁剪，如存在）
    'https://media.githubusercontent.com/media/djlorenz-research/lightpollution/master/data/china_wa.tif',
    'https://github.com/lightpollutionmap/data/raw/main/china_viirs_2022.tif',
    # NOAA VIIRS VNL annual（全球 15 arcsec 压缩 tif，较大）
    'https://ngdc.noaa.gov/eog/data/web_data/v4/2015_annual/china.tif',
]


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    if os.path.exists(OUT) and os.path.getsize(OUT) > 1e6:
        print('already exists:', OUT)
        return
    for url in SOURCES:
        try:
            print('try:', url, flush=True)
            r = requests.get(url, timeout=180, stream=True)
            r.raise_for_status()
            tmp = OUT + '.part'
            total = 0
            with open(tmp, 'wb') as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
                    total += len(chunk)
            if total > 1e6:
                os.replace(tmp, OUT)
                print(f'saved {total / 1e6:.1f} MB -> {OUT}')
                return
            os.remove(tmp)
            print('too small, skip')
        except Exception as e:
            print('fail:', str(e)[:100], flush=True)
    print('\n所有候选源失败。可手动下载 World Atlas 2015 tif（中国区域，'
          '需含 ModelPixelScale/ModelTiepoint tag，辐射亮度 nW/cm^2/sr）'
          f'并放置到：\n  {OUT}\n未放置时系统使用启发式光污染估算（已可用）。')


if __name__ == '__main__':
    main()
