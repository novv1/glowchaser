# 逐光者 GlowChaser

火烧云与悬日月预测系统（珠三角优先）。

**线上**：https://7a8a63a8cc8c497aa624f04d87567517.app.workbuddy.link
**本地**：`http://localhost:8000`（桌面快捷方式「逐光者.bat」）

## 快速开始

```bash
# 1) 安装依赖（Python 3.11+）
python -m pip install -r app/requirements.txt

# 2) 配置凭据（见下方「配置」章节；仅本地全能力态需要）

# 3) 启动服务
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# Windows 也可双击 scripts\GlowChaser-Launcher.bat
```

打开浏览器访问 `http://localhost:8000`，选择城市/日期/朝霞或晚霞 → 查询。

> ⚠️ **改完后端代码要重启服务**：uvicorn 不带 `--reload` 时 Python 模块常驻内存
> （静态文件仍每次从磁盘读，所以会出现「页面是新的、接口是旧的」）。页面会比对
> 前后端版本并在不一致时提示重启。

## 功能一览

| 功能 | 说明 |
|---|---|
| 8 维评分 | 中高层云/光路/降水/能见度/AOD 等，555 天回测校准（AUC 0.719） |
| 今日简报 | 规则模板把判定/时段/依据/风险讲成一段结论前置的人话（零 AI 成本、无幻觉） |
| 温度探空 | T/Td 廓线（5 气压层 + 地面，零新增 HTTP）+ 0°C 层：气象网格、大气截面与独立探空图三处呈现；T−Td ≤ 2°C 判近饱和（云实不易幡状） |
| 受光（地影）判据 | 云只在其所在地太阳高度角高于 −b(h)（b=acos(R/(R+h))）时才算霞光画布：光路按真实层高逐层折算「受光画布」，反烧只统计仍受光的高云；截面图给出地影线与逐层变暗时刻（`GC_ILLUM=0` 可退回旧口径） |
| 集合不确定区间 | 50 成员中高云量 P10/P50/P90 分位带 + 中位与半宽，写进结论区 |
| 多模式共识 | ECMWF/GFS/ICON/CMA + 50 成员集合 |
| 火烧云地图 | 0.25° 省轮廓网格，概率分/质量分切换 |
| 反烧地图 | 反日方向高云照亮区 |
| 大气截面图 | 光路 9 城市湿度剖面 + 分段光路分析 + 地影线（随时间轴下沉） |
| 卫星临近校准 | JMA B13 + JAXA CLP 云型/光学厚度（T-6h 自动出现）；**10 分钟时间轴**可拖看云带移动；光路按目标方位取景并标比例尺/指北针/目标端；**光路逐段按云判读着色（红=云 绿=隙）并标注云带/云隙距离段**；如实标注观测时次与发布时次、本地分辨率（JMA 601px 在珠三角约 26.9km/像素）、光路云带与云隙 |
| 悬日月热力图 | 地平线可见性概率地图 |
| 悬日月拍摄 | 标准底图（高德/Esri/OSM 可切换）搜目标点或点选 → 自动定方位；只答所选日期能否拍 + 日月可见性；**已过窗口会让位给未来窗口**（不再拿今天早晨的窗口当结论） |
| 下次对准 | 未来 13 个月逐日扫描升落方位（日/月 × 出/落，与窗口表同一套天文算法），回答「明早行不行、不行是哪天」，点日期即跳转重评 |
| 高度精细化 | 机位海拔（可见地平线下沉、遮蔽云距收缩）+ 目标海拔（反推目标仰角，含地球曲率） |
| 方向天气可视化 | 天空实景预览（日月真实位置 vs 取景框）、视线剖面（云层/交点/遮挡段）、当日轨迹+取景带、多模式一致性点图 |
| 天体预览（相机画面） | 画布就是相机画面（焦距可设 24–1200mm，与视场角双向同步；HUD 直接给「日面占画面宽 %」）；日/月圆盘按真实角径与轨迹落位、亮面朝向随太阳方位；云带落在「视线与该层交点」的真实仰角上、透明度与该层视线云量同口径（可见度 = 后端口径）；山脊按 16 方位真实遮蔽角、能挡住日面；天空底色随该时刻真实太阳高度角推演（悬月也有白天/暮色天空）；±30 分钟滑块 + 轨迹弧 + "多久移出画面" |
| 地标搜索 | 输入即联想（防抖+请求取消+缓存），空格多关键字模糊匹配；本地库 → Photon(OSM POI) → Nominatim 兜底，免 key |
| 视线遮挡诊断 | 按真实云层高度（ECMWF 逐气压层）算视线-云壳交点距离，判断哪层云在多远挡住天体；沿目标方位远端采样云况与光路 AOD |
| Planit .pft 评估 | 导入巧摄文件，扫描 90 天悬日月窗口 + 天气判定 |
| 7 天概览 | 未来 7 天逐日概率 |
| 验证日志 | 预报自动落盘 + 五档实况回填 → 校准数据积累 |
| 主题 | **单一主题「日暮片场」**（2026-10-23 收敛）：近黑片场底 + 金/青双色调 + 米白字幕，不再有皮肤与明暗切换 |
| 视觉层 | 全幅实时天空帧（场景引擎按预报云属/云量/受光/太阳高度角渲染首屏）+ 答案层（一句人话结论 / 概率 / 判定徽标 / 决策带）、胶片颗粒与暗角、章节折叠、滚动进度光带、回到顶部 |
| 加载与错误态 | 骨架屏 + 进度光带 + 已等待计时 + 取消/重试；失败时为独立错误卡片，不再与报告同时出现；底图未就绪时地图卡退化说明，不拖垮整份报告 |
| 深链与路由 | `#/glow/<tab>`、`#/align` 携带城市/日期/朝晚霞，可刷新保持、后退还原、整条链接分享 |
| 动效 | 卡片错峰入场、概率环生长 + 数字滚动、Tab 滑动指示器、顶栏毛玻璃与滚动收窄、卡片跟随光标的柔光、按钮水波纹；尊重系统「减少动态效果」 |
| 移动端 | ≤640px 时 Tab 变图标底部导航、顶栏滚动后收起次要控件（4 行 → 2 行），固定底栏不遮内容 |

## 数据源

| 层级 | 来源 | 免费 |
|---|---|---|
| 模式预报 | Open-Meteo（ECMWF/GFS/ICON/CMA + 50 成员集合） | ✅ |
| 云层高度/云量 | Open-Meteo 气压层 `cloudcover_*hPa` + `geopotential_height_*hPa`（1000~200 hPa，真实层高；ECMWF 主判 + GFS 复核） | ✅ |
| 气溶胶 | Open-Meteo Air Quality（CAMS AOD 550nm + PM2.5） | ✅ |
| 卫星红外 | JMA MSC B13 全圆盘（10 分钟节奏） | ✅ |
| 卫星云特性 | JAXA P-Tree CLP（云型/光学厚度/云顶温） | ✅ FTP |
| 地面实况 | 和风天气（总云量/能见度/分钟级降水） | ✅ |

> ⚠️ **Open-Meteo 免费额度是按 IP 计的（10000 次/日）**，不是按账号。若出口是共享 IP
> ——云主机、或运营商的运营商级 NAT（CGNAT，国内宽带/移动网络大量如此）——额度会被
> 同一 IP 背后的其他用户一起消耗，表现为 `429 Daily API request limit exceeded`，
> 且与自己的用量无关。
>
> 遇到时的表现：接口返回 **429 + 可读原因**（而非 500），页面**回退到最近一次缓存的报告**
> 并说明原因与生成时刻；只有在没有缓存时才弹错误卡。服务端做了 429 分级（日额度立刻
> 放弃、不重试）、进程内冷却（冷却期零请求、不 sleep）、缓存落盘（`data/om_cache/`，
> 默认 TTL 3 小时，可用 `GC_OM_CACHE_TTL` 覆盖）——所以额度没了也不会把请求打成风暴。
>
> 要把额度拿回自己手里，只有走 API key：`customer-api.open-meteo.com`（额度按 key 计、
> 不按 IP）。诊断可看 `/api/health`（会给出 `limited(daily)，冷却剩余 Ns`）。

## 项目结构

```
<项目根>\
├── app/                    # FastAPI 应用（可独立部署）
│   ├── main.py             # 入口 + 路由
│   ├── start.py            # 沙箱启动入口（读 PORT）
│   ├── core/               # 评分引擎/天文/几何/视线遮挡几何/城市库/日志配置
│   ├── services/           # Open-Meteo/卫星/和风/报告/日志
│   ├── static/             # 前端（ECharts + Leaflet + 原生 JS）
│   ├── requirements.txt    # 运行时依赖
│   └── config_private.example.json # 凭据模板（真实凭据走环境变量）
├── docs/                   # 文档 + 参考资料
├── tests/unit/             # pytest 单元测试（离线，无网络）
├── tests/visual/           # 前端回归与截图（Playwright + Edge）
├── tests/backtest/         # 回测校准脚本 + 数据
├── data/                   # 运行时数据（日志/缓存，不入 Git）
├── requirements-dev.txt    # 测试/静态检查依赖
├── pyproject.toml          # pytest 配置
└── .github/workflows/ci.yml# CI（pyflakes + pytest）
```

## 配置（凭据）

凭据**不入 Git**。优先级：环境变量 > `app/config_private.json`（本地开发可复制
`app/config_private.example.json` 后填写）。

```bash
# 和风天气（地面实况）
export QWEATHER_HOST="https://xxxxx.re.qweatherapi.com"
export QWEATHER_KEY="你的 API Key"

# JAXA P-Tree FTP（卫星云特性，仅本地全能力态需要）
export PTREE_HOST="ftp.ptree.jaxa.jp"
export PTREE_USER="你的账号"
export PTREE_PASS="你的密码"
```

> ⚠️ 历史提交里曾包含真实凭据，**请务必先到各平台轮换/重置 Key 与 FTP 密码**，
> 再清理 Git 历史（见下方「安全提醒」）。

## 测试与静态检查

```bash
python -m pip install -r requirements-dev.txt
python -m pytest          # tests/unit 下 130 个离线单测
python tests/visual/logic_check.py  # 前端逻辑回归（Playwright + Edge，226 项）
python tests/visual/ui_audit.py     # 交互层闸门：折叠高度 / 横向溢出 / 控制台报错 / 三档截图
python -m pyflakes app/   # 静态检查
```

截图工具（需 Playwright + Edge，`channel=msedge`）：

```bash
# 霞光 / 悬日月整页截图（--mock 走离线 fixture，不联网）
python tests/visual/capture.py --mode glow --mock
python tests/visual/capture.py --mode align --mock --dark --az 276

# 唯一主题的令牌生成（预览 = 实现，同一份色值）
python tests/visual/theme_preview.py --emit-css   # 输出 style.css 的 :root 令牌块

# 交互状态出图：加载 / 已等待 / 取消 / 错误 / 缓存回退 / 滚动收窄 / 卡片柔光 / 切 Tab / 窄屏
python tests/visual/ui_states.py

# Hero 白字对比度体检（按 WCAG 相对亮度算，改配色后跑一遍）
python tests/visual/hero_contrast.py
```

> 主题只有一套，令牌仍由 `theme_preview.py --emit-css` 生成后贴入 `style.css`（改配色改 `vars_of()`，不要手改令牌），
> **改配色或加深色令牌请改脚本里的 `vars_of()` 再重新生成**，不要直接手改那段 CSS
> —— 否则预览与实现会分叉。重生成后可这样贴回去（保留样式表其余部分）：
>
> ```bash
> python - <<'PY'
> import io, sys; sys.path.insert(0, 'tests/visual')
> import theme_preview
> p = 'app/static/style.css'; s = io.open(p, encoding='utf-8').read()
> a = s.index('/* ── 由 tests/visual/theme_preview.py')
> b = s.index('html[data-theme="dark"] .theme-dark-only')
> io.open(p, 'w', encoding='utf-8', newline='\n').write(
>     s[:a] + theme_preview.emit_css() + '\n\n' + s[b:])
> PY
> ```
>
> 环境光层的对照排查工具：`tests/visual/amb_probe.py`（逐层关闭环境光再截图，
> 用来定位块状接缝之类的合成问题）。

## 珠三角验证（卫星自动打标）

现有 555 天回测是**上海**、标签来自 sunsetbot（`sb_labels.json`），珠三角没有标签。
`tests/backtest/` 这套工具用**向日葵卫星观测**自动生成珠三角的客观标签，全程无人工：

```bash
# 1) 拉取 B13 珠三角窗口（P-Tree FTP；需 PTREE_* 凭据，见「配置」）
python tests/backtest/b13_grid.py --start 2026-04-01 --end 2026-09-30 \
    --period both --offsets=-10,10 --jobs 8

# 2) 生成预报分：ERA5 再分析当「完美预报」，引擎同口径光路打分
#    （与上海 0.719 基线同口径，衡量评分函数的判别力上限）
python tests/backtest/prd_scores.py --start 2026-04-01 --end 2026-09-30 \
    --period both

# 3) 反演「受光画布」并落标签 CSV（data/glow_labels.csv）
#    默认 offsets 已是超集（-10,0,10,20,30），不存在的槽位自动跳过，
#    不会与下载时的偏移错位
python tests/backtest/auto_labels.py --start 2026-04-01 --end 2026-09-30 \
    --period both

# 4) 与预报分对账（AUC / Brier / 校准分箱 / 高分命中率）
python tests/backtest/validate_prd.py --labels data/glow_labels.csv \
    --forecast data/prd_forecast_scores.csv
#    对账 Brier 用上海曲线；要看珠三角曲线（v2.35 接入线上）的口径加 --region prd

# 5) （可选）重拟合珠三角校准曲线：分箱烧率 -> PAVA -> 基率缩放，
#    时间留出 8~9 月防过拟合；产出 tests/backtest/refit_prd.json
python tests/backtest/refit_calibration_prd.py
```

判定与线上**同一套**：`app/core/illumination.lit_fraction` 的受光（地影）判据
+ `app/core/scoring.dim2_path_canvas` 的曲线与低云惩罚。区别只在输入——线上喂预报云场，
这里喂卫星反演的观测云场（B13 亮温 → 云顶高度）。所以它检验的是「云场是否满足受光条件」
这个**必要条件**，不等于「肉眼是否看到橙红色」（颜色还看气溶胶等，卫星给不出）。

四个坑已经踩过并写进代码注释，换源/换年时别再走一遍：

- **JAXA CLP 云特性 L2**（0.5 MB/帧，本该最省）依赖可见光反演，日落前后整片缺测
  （实测珠三角 19:10/19:40 CST 为 100% 无效），**恰好错过霞的时段**，不能用。
- **P-Tree 的 `tbb_*` 是开尔文**，不是摄氏度；混用会把整片天空静默判成晴空。
- **2026 起全圆盘网格改名**：`R21_FLDK.02401_02401` → `R21_FLDK.02801_02401`
  （经度轴东扩到 210°E，行数与 0.05° 间距不变，按范围裁窗不受影响）；同槽位还挂着
  0.02° 的 `07001_06001`（550MB+/帧，别选）。`b13_grid.py` 已按列数优选 02401 档。
- **朝霞跨 UTC 日界**：日出 06:02 CST = **前一天** 22:02 UTC，帧挂在前一天的目录里。
  `slot_time` 现在返回 UTC 日期、下载与打标共用——PM 恰好同日，只拿晚霞冒烟测不出
  这个 bug（2026-10 修复；此前 AM 一直「无数据」）。

体量与速度（2026-10 实测）：全圆盘一帧 115MB，裁出窗口只 0.2 MB。实测单连接约
4 MB/s（一帧 30s 上下），**8 路并发摊薄约 20~40s/帧，半年（736 帧）约 4~6 小时**，
**请当后台批任务跑**，可随时中断，`.part` 断点续传、npz 自动跳过已缓存。
（旧测量「单连接 110 KB/s、一帧 14 分钟」已过时，2026-10 复测快了一个数量级。）

**首批结果（2026-04-01~09-30，366 天次）**：AUC **0.745**（上海 555 天基线 0.719，
同口径）——评分函数在珠三角的判别力成立，dim2 曲线无需紧急重拟合；≥85 分的
92.3% 卫星判定有霞画布；Brier 0.289 差于基率 0.174 → 根因是 CALIB_PTS 概率映射
按上海基率拟合（PRD 事件基率 77.6% vs 上海 ~30%）。注意标签是
「受光画布 ≥30%」这个必要条件，不是肉眼大烧；详见 `docs/方案设计.md` v2.32。

**v2.35 已完成概率映射重拟合**：`refit_calibration_prd.py`（PAVA + 基率缩放，
时间留出 8~9 月），新曲线 Brier 0.152（留出集 0.170，均优于基率预报 0.174；
旧上海曲线 0.289）。已按**地区分流**接入线上——机位在珠三角框（`scoring.PRD_BOX`）
内走 `CALIB_PTS_PRD`，判定词/配色阈值同源分流；上海侧曲线与数值完全不变。
曲线语义变为「受光画布 ≥30% 的概率」，判定词阈值相应重定（不烧<50/微烧<65/
小烧<75/中烧<85/大烧）。

> 别用 `pkill` 停它（Git Bash 里没有这个命令，会静默失败并留下两个进程同时往
> 同一个 `.part` 追加、把文件写坏）。脚本自带单实例锁 `data/b13_grid/.lock`，
> 重复启动会直接拒绝并提示 pid。

> `validate_prd.py` 的预报分从哪来最容易做错：**别用「今天调的预报接口去查历史日期」**
> ——那拿到的是当前模式对过去的分析场，等于开上帝视角，AUC 是假的。要么用 ERA5 再分析
> 衡量评分函数的上限（与上海 0.719 基线同口径），要么用归档的预报场 / `forecast_log` 落盘。
> 注意免费档案接口只给 `temperature_2m`，**气压层变量一律返回 null**，所以廓线是
> 「T2m 锚定标准大气」（季节由 T2m 带进来），拿不到时才退回固定标准大气。

CI 配置见 `.github/workflows/ci.yml`（push / PR 自动跑 lint + 单测）。

## 使用指南

详见 `docs/使用指南.md`。

## 安全提醒

`app/config_private.json` 曾在旧提交中被跟踪。即使现在已停止跟踪，历史提交仍可
检出明文凭据。轮换凭据后，可用 `git filter-repo --path app/config_private.json
--invert-paths`（或 BFG）清理历史；本仓库当前无 remote，清理成本很低。

## 技术文档

详见 `docs/方案设计.md`（含变更记录 v1.0-v2.30）。
