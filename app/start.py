# -*- coding: utf-8 -*-
"""发布沙箱启动入口：读取 PORT 环境变量"""
import os
import uvicorn

if __name__ == '__main__':
    uvicorn.run('main:app', host='0.0.0.0',
                port=int(os.environ.get('PORT', 8000)))
