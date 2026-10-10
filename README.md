# US Market Dual-Signal Bot — 美股双指数信号自动推送

同时监控**标普500**和**纳斯达克100**的风险信号，触发阈值时自动推送企业微信通知。

## 双指数六大信号

### 标普500 (SPY + VIX + CAPE)

| 信号 | 指标 | 说明 |
|------|------|------|
| SPY 回撤 | SPY ETF 从峰值回撤幅度 | 回撤超过阈值（默认 -10%）→ 大盘回调风险 |
| VIX 恐慌指数 | 标普500期权隐含波动率 | VIX 超过阈值（默认 30）→ 市场恐慌情绪 |
| Shiller CAPE | 标普500周期调整市盈率 | CAPE 超过阈值（默认 30）→ 估值偏高 |

### 纳斯达克100 (QQQ + VIX代理 + PE)

| 信号 | 指标 | 说明 |
|------|------|------|
| QQQ 回撤 | QQQ ETF 从峰值回撤幅度 | 回撤超过阈值（默认 -10%）→ 科技股回调风险 |
| VIX恐慌(VXN代理) | VIX 代理（VXN数据源不稳定） | VIX 超过阈值（默认 30）→ 纳斯达克恐慌 |
| QQQ PE估值 | QQQ ETF 市盈率 | PE 超过阈值（默认 35）→ 科技股估值偏高 |

两组信号合并在一条企业微信消息中推送。

## 项目结构

```
nasdaq-signal-bot/
├── signal_bot.py       # 核心逻辑：双指数数据获取、信号判断、合并推送
├── config.yaml         # 配置：Webhook 地址 + 双组阈值
├── requirements.txt    # Python 依赖
└── README.md           # 本文件
```

## 本地运行

### 1. 安装依赖

```bash
cd /Users/mengxiang/Documents/workbuddy/nasdaq-signal-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置 Webhook

编辑 `config.yaml`，将 `webhook_url` 替换为你的企业微信群机器人地址：

```yaml
webhook_url: "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=你的实际KEY"
```

> 获取方式：企业微信群 → 右上角群设置 → 群机器人 → 添加机器人 → 复制 Webhook 地址

### 3. 运行

```bash
# 仅信号触发时推送告警（默认行为）
python signal_bot.py

# 每次运行都推送完整报告
python signal_bot.py --always
```

### 4. 调整阈值

编辑 `config.yaml`，分别调整标普500和纳斯达克100的阈值：

```yaml
sp500_thresholds:
  drawdown: -10.0   # SPY 回撤阈值
  vix: 30.0         # VIX 阈值
  cape: 30.0        # CAPE 阈值

nasdaq100_thresholds:
  drawdown: -10.0   # QQQ 回撤阈值
  vxn: 30.0         # VIX代理阈值
  qqq_pe: 35.0      # QQQ PE 阈值
```

## 部署到腾讯云函数（SCF）

### 步骤一：准备代码包

```bash
cd /Users/mengxiang/Documents/workbuddy/nasdaq-signal-bot

mkdir -p deploy_package
cp signal_bot.py deploy_package/
cp config.yaml deploy_package/
cp requirements.txt deploy_package/

cd deploy_package
pip install -r requirements.txt -t .

zip -r nasdaq-signal-bot.zip .
```

### 步骤二：创建云函数

1. 登录 [腾讯云函数控制台](https://console.cloud.tencent.com/scf)
2. 点击「新建」→ 选择「从头开始」
3. 配置：
   - **函数名称**: `nasdaq-signal-bot`
   - **运行环境**: Python 3.9 或 Python 3.10
   - **执行内存**: 256MB
   - **执行超时**: 60秒
   - **上传方式**: 本地上传 zip 包 → 选择 `nasdaq-signal-bot.zip`
4. **处理程序**: `signal_bot.main_handler`
5. 点击「完成」创建函数

### 步骤三：配置环境变量（推荐）

在函数「函数管理 → 函数配置 → 环境变量」中添加：

| Key | Value | 说明 |
|-----|-------|------|
| `WEBHOOK_URL` | `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=你的KEY` | 企业微信 Webhook |
| `SPY_DD_THRESHOLD` | `-10.0` | SPY 回撤阈值 |
| `VIX_THRESHOLD` | `30.0` | VIX 阈值 |
| `CAPE_THRESHOLD` | `30.0` | CAPE 阈值 |
| `QQQ_DD_THRESHOLD` | `-10.0` | QQQ 回撤阈值 |
| `VXN_THRESHOLD` | `30.0` | VXN(VIX代理)阈值 |
| `QQQ_PE_THRESHOLD` | `35.0` | QQQ PE 阈值 |

> 设置环境变量后，config.yaml 中的同名配置会被覆盖。

### 步骤四：配置定时触发器

在函数「触发管理 → 创建触发器」：

- **触发方式**: 定时触发
- **触发周期**: 自定义 → `0 0 22 * * 1-5 *`（周一到周五北京时间 22:00）
- **Cron 表达式说明**: 腾讯云函数使用 7 位 Cron：`秒 分 时 日 月 周 年`
  - `0 0 22 * * 1-5 *` = 每周一到周五 22:00 执行（美股盘中）
  - `0 0 6 * * 2-6 *` = 每周二到周六 06:00 执行（美股收盘后次日早晨）

### 步骤五：测试

在函数控制台点击「测试」，使用以下测试事件：

```json
{
  "always_push": true
}
```

这会强制推送一次完整报告，验证 Webhook 是否正常工作。

## 常见问题

### Q: yfinance 在云函数中报错？

云函数网络环境可能无法直连 Yahoo Finance API。解决方案：
1. 确保云函数有公网访问权限（默认开启）
2. 如果仍失败，可在腾讯云控制台开启 NAT 网关
3. 或将 yfinance 替换为其他数据源 API

### Q: CAPE 获取失败显示 -1？

multpl.com 可能临时不可达。代码已做容错处理，CAPE 获取失败时会跳过该信号判断。可手动在 [multpl.com/shiller-pe](https://www.multpl.com/shiller-pe) 查看当前值。

### Q: QQQ PE 获取失败？

yfinance 的 `info` 字段偶尔返回空值。代码已做容错处理，PE 获取失败时会跳过判断。

### Q: VXN 为什么用 VIX 代理？

VXN（纳斯达克波动率指数）的 yfinance 数据源不稳定，经常无法获取。标普和纳指高度相关，VIX 作为通用恐慌指标对纳指也有很好的预警作用。如果未来 VXN 数据源改善，可以切换回 VXN。

### Q: 企业微信消息没收到？

1. 检查 `config.yaml` 或环境变量中 `webhook_url` 是否正确
2. 检查机器人是否被移出群
3. 企业微信机器人有频率限制：每分钟最多 20 条消息

### Q: 如何同时推送多个群？

可在云函数控制台创建多个函数实例，每个实例使用不同的 Webhook URL。
