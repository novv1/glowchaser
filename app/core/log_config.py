# -*- coding: utf-8 -*-
"""统一日志配置：库内模块用 logging.getLogger(__name__)，进程入口调用 setup_logging()。

日志级别可用环境变量 GLOWCHASER_LOG_LEVEL 覆盖（DEBUG/INFO/WARNING/ERROR）。
"""
import logging
import os


def setup_logging(level: str = None) -> None:
    lvl_name = (level or os.environ.get('GLOWCHASER_LOG_LEVEL') or 'INFO').upper()
    lvl = getattr(logging, lvl_name, logging.INFO)
    root = logging.getLogger()
    if root.handlers:          # 已被 uvicorn/测试框架配置过：只调级别，不重复加 handler
        root.setLevel(lvl)
        return
    logging.basicConfig(
        level=lvl,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
