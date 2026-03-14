## CME 多标的实时行情监控工具 · 从零到跑起来

> 本教程面向完全没有折腾过 Python 量化环境的同学。  
> 从头到尾跟着做，遇到报错先对照最后的「救命！报错了」章节。

---

## 目录

1. [你需要准备什么](#1-你需要准备什么)
2. [第一步：安装 Miniconda（Python 环境管理器）](#2-第一步安装-miniconda)
3. [第二步：下载 / 拿到项目文件](#3-第二步下载--拿到项目文件)
4. [第三步：创建专属 Python 环境 & 安装依赖](#4-第三步创建专属-python-环境--安装依赖)
5. [第四步：安装并配置 IBKR TWS（模拟账户）](#5-第四步安装并配置-ibkr-tws)
6. [第五步：修改合约月份配置（重要！）](#6-第五步修改合约月份)
7. [第六步：启动程序！](#7-第六步启动程序)
8. [第七步：看懂界面](#8-第七步看懂界面)
9. [数据查询（可选）](#9-数据查询可选)
10. [救命！报错了](#10-救命报错了)

---

## 1. 你需要准备什么

| 项目 | 说明 |
|------|------|
| 电脑系统 | macOS 10.15+ 或 Windows 10/11 或 Linux |
| 网络 | 能访问 pypi.org 装包（如果慢，后面有换国内镜像的方法） |
| IBKR 账户 | 有模拟（Paper）账户即可，不需要真钱 |
| 终端工具 | macOS 用系统自带「终端」或 iTerm2；Windows 用「PowerShell」或「Windows Terminal」|
| 时间 | 第一次大约 30–60 分钟 |

> **完全不需要**：numpy、scipy、任何数据库客户端。项目自带纯 Python 计算 + DuckDB。

---

## 2. 第一步：安装 Miniconda

Miniconda 是一个轻量 Python 环境管理器，让你可以同时装多个互不干扰的 Python 版本。

### macOS

1. 打开终端（Spotlight 搜索 "Terminal"）
2. 复制粘贴下面这条命令，按回车：

```bash
# Intel Mac
curl -O https://repo.anaconda.com/miniconda/Miniconda3-latest-MacOSX-x86_64.sh
bash Miniconda3-latest-MacOSX-x86_64.sh

# Apple Silicon (M1/M2/M3)
curl -O https://repo.anaconda.com/miniconda/Miniconda3-latest-MacOSX-arm64.sh
bash Miniconda3-latest-MacOSX-arm64.sh
```

3. 一路按 `Enter`，遇到 `yes/no` 的地方都输入 `yes`
4. **关闭终端，重新打开**（这步很重要，让 conda 命令生效）
5. 验证安装成功：

```bash
conda --version
# 应该显示类似：conda 24.x.x
```

### Windows

1. 去 [https://docs.conda.io/en/latest/miniconda.html](https://docs.conda.io/en/latest/miniconda.html) 下载 Windows 64-bit 安装包
2. 双击 `.exe` 安装，全部点"Next"，**勾选 "Add Miniconda3 to my PATH"**
3. 安装完成后打开「Anaconda Prompt」（开始菜单里搜索）
4. 验证：

```bash
conda --version
```

---

## 3. 第二步：下载 / 拿到项目文件

向你的朋友（项目作者）要整个 `project` 文件夹，或者通过 Git 下载：

```bash
# 如果用 Git（有 git 的情况下）
git clone <项目地址> /Users/你的用户名/project

# 如果是直接拷贝的压缩包，解压到某个位置
# 解压后确认目录结构是这样的：
# project/
# └── intraday/
#     ├── main.py
#     ├── config/
#     ├── core/
#     └── ...
```

记住你的项目所在路径，比如 `/Users/xiaomei/project`，下面会用到。

---

## 4. 第三步：创建专属 Python 环境 & 安装依赖

打开终端（macOS）或 Anaconda Prompt（Windows），依次执行：

### 4.1 创建环境

```bash
conda create -n quant python=3.10 -y
```

> 这里 `quant` 是环境名字，可以随便取。`-y` 表示自动确认，省得一直按 yes。

### 4.2 激活环境

**方式一（推荐，兼容性最好）**：用环境完整路径前缀执行命令，无需事先激活：

```bash
conda run -p /path/to/envs/quant python -m intraday.main
# 将 /path/to/envs/quant 替换为你实际创建环境时指定的路径（安装时 -p 参数的值）
```

创建环境时可以指定路径，方便日后引用：

```bash
conda create -p ~/quant_env python=3.10 -y
# 之后用：conda run -p ~/quant_env python ...
```

**方式二**：如果 conda 可以识别环境名（通常在默认位置创建时），用名称激活：

```bash
conda activate quant
# 激活成功后命令行左边会出现 (quant)
```

> 如果 `conda activate quant` 报 `EnvironmentNameNotFound`，说明环境不在默认路径，改用方式一。

### 4.3 安装依赖包

```bash
pip install ib_insync pytz rich duckdb
```

> 如果下载很慢，换国内镜像：
> ```bash
> pip install ib_insync pytz rich duckdb -i https://pypi.tuna.tsinghua.edu.cn/simple
> ```

安装完验证一下：

```bash
python -c "import ib_insync; import rich; import duckdb; print('全部OK！')"
# 应该显示：全部OK！
```

---

## 5. 第四步：安装并配置 IBKR TWS

这个程序需要连接 Interactive Brokers（盈透证券）的交易软件 TWS 来获取实时行情。

### 5.1 下载 TWS

去官网下载 Trader Workstation（TWS）：  
[https://www.interactivebrokers.com/en/trading/tws.php](https://www.interactivebrokers.com/en/trading/tws.php)

选择适合你系统的安装包，安装后用你的 IBKR **模拟账户（Paper Account）** 登录。

> 没有 IBKR 账户？去官网注册，开通模拟账户是免费的。

### 5.2 开启 TWS 的 API 接口

TWS 默认不开放 API，需要手动开启：

1. TWS 登录后，点击顶部菜单 **Edit → Global Configuration**
2. 左侧找到 **API → Settings**
3. 按照下图勾选：

```
✅ Enable ActiveX and Socket Clients   ← 必须勾选
✅ Allow connections from localhost only
Socket port:  7497                     ← 模拟账户用 7497，实盘用 7496
Master API client ID: 0
```

4. 点击 **Apply**，然后 **OK**
5. **不要关闭 TWS**，保持运行状态

> **每次运行本程序前，TWS 必须处于登录并运行的状态！**

---

## 6. 第五步：修改合约月份

CME 期货合约有到期日，你需要确认配置的合约月份没有过期。

用任意文本编辑器（VS Code / TextEdit / 记事本）打开：

```
project/intraday/main.py
```

找到这一段（大约在第 32–38 行）：

```python
SYMBOLS = [
    SymbolSpec(GC_CONFIG, last_trade_date="202604"),
    SymbolSpec(ES_CONFIG, last_trade_date="202603"),
    SymbolSpec(NQ_CONFIG, last_trade_date="202603"),
]
```

根据当前日期修改为未来的合约月份，比如现在是 2026 年 2 月：

```python
SYMBOLS = [
    SymbolSpec(GC_CONFIG, last_trade_date="202606"),   # 黄金 GC，2026年6月合约
    SymbolSpec(ES_CONFIG, last_trade_date="202606"),   # 标普 ES，2026年6月合约
    SymbolSpec(NQ_CONFIG, last_trade_date="202606"),   # 纳指 NQ，2026年6月合约
]
```

> **最简单的方式：把 `last_trade_date` 改成空字符串 `""`，程序会自动选最近的主力合约：**
> ```python
> SYMBOLS = [
>     SymbolSpec(GC_CONFIG, last_trade_date=""),
>     SymbolSpec(ES_CONFIG, last_trade_date=""),
>     SymbolSpec(NQ_CONFIG, last_trade_date=""),
> ]
> ```

保存文件。

---

## 7. 第六步：启动程序！

### 7.1 进入项目的父目录

```bash
# macOS / Linux
cd /Users/你的用户名/project    # ← 替换成你的实际路径，注意是 intraday 的上一级目录

# Windows
cd C:\Users\你的用户名\project
```

**注意：是 `project/` 这个目录，不是 `project/intraday/`！**

### 7.2 启动（含 conda 环境）

**推荐方式**（无论环境在哪里都能用）：

```bash
conda run -p ~/quant_env python -m intraday.main
# 替换 ~/quant_env 为你创建环境时用的路径
```

或先激活再运行（环境在默认位置时）：

```bash
conda activate quant
python -m intraday.main
```

### 7.4 正常启动应该看到

```
◈ 连接 TWS 7497  (GC / ES / NQ)...
  ✅ GC    → GCM6
  ✅ ES    → ESM6
  ✅ NQ    → NQM6
```

然后终端变成全屏 TUI 界面，开始实时显示行情数据。

> **按 `Ctrl + C` 退出程序**，退出后会自动保存数据。

---

## 8. 第七步：看懂界面

启动后你会看到一个多栏终端界面，大致如下：

```
┌─ 状态栏 ─────────────────────────────────────────────────────────┐
│  GC  2026-02-19 14:32:05   时段: Euro_US_Overlap                 │
├─ GC 黄金  ──────┬─ ES 标普500 ──────┬─ NQ 纳指 ─────────────────┤
│ VWAP  2950.20  │ VWAP   5880.50   │ VWAP  20955.00             │
│ Δ Ratio  +0.32 │ Δ Ratio  -0.15   │ Δ Ratio  +0.55             │
│ Impact  3.2bps │ Impact  1.8bps   │ Impact  2.1bps             │
│ ...            │ ...              │ ...                         │
├─ 信号告警 ─────────────────────────────────────────────────────── ┤
│ [WARN]  ES  买卖失衡  delta_ratio=0.68                           │
└──────────────────────────────────────────────────────────────────┘
```

### 关键指标速查

| 指标 | 看什么 | 怎么判断 |
|------|--------|---------|
| **VWAP** | 成交量加权均价 | 价格偏离 VWAP 越远，回归概率越高 |
| **Δ Ratio** | 买卖力量对比，范围 -1 到 +1 | 正数 = 买方主导，负数 = 卖方主导 |
| **Impact bps** | 市场冲击成本（基点） | 突然变大 = 流动性变差，小心滑点 |
| **σ (CLT)** | 价格扩散速度（波动率） | 越大 = 波动越剧烈 |
| **超额峰度** | 厚尾风险 | >5 容易出现跳空或极端行情 |
| **信号告警** | 红/黄色提示 | `[ALERT]` 是高危，`[WARN]` 是预警 |

### 信号含义

| 信号名称 | 意思 |
|---------|------|
| `冲击突增` | 流动性骤降，交易成本上升 |
| `厚尾加剧` | 极端行情概率升高，要小心 |
| `买卖失衡` | 单边力量很强，可能有趋势发展 |
| `成交量异常` | 成交量是平均值 3 倍以上，有大资金介入 |
| `流动性枯竭` | 没有成交，不要轻易挂单 |

---

## 9. 数据查询（可选）

程序运行时会自动把数据存到本地数据库 `~/results/intraday.duckdb`。

### 交互式菜单（最方便）

```bash
cd /path/to/project
conda run -p ~/quant_env python -m intraday.query
```

会出现一个菜单，选数字即可查询各种统计。

> **主程序跑着的时候也可以查询！** 脚本检测到数据库被占用时会自动复制一份快照来读，不影响主程序正常写入。出现 `⚠️ 主程序运行中，已复制快照` 是正常提示。

### 常用命令行查询

```bash
# 今日各标的汇总
conda run -p ~/quant_env python -m intraday.query --summary

# 查看 ES 最新 30 条记录
conda run -p ~/quant_env python -m intraday.query --symbol ES --tail 30

# 查看最近 3 小时 GC 的衰减趋势
conda run -p ~/quant_env python -m intraday.query --symbol GC --decay 3

# 查看厚尾风险时段
conda run -p ~/quant_env python -m intraday.query --risk

# 数据库行数统计
conda run -p ~/quant_env python -m intraday.query --count

# 导出今日数据为 Parquet 文件
conda run -p ~/quant_env python -m intraday.query --export 20260219
```

---

## 10. 救命！报错了

### ❌ `conda: command not found`

→ Miniconda 没装好，或者终端没重启。关掉终端重新打开，或者重新安装 Miniconda。

---

### ❌ `ModuleNotFoundError: No module named 'ib_insync'`

→ 依赖没装，或者没激活 conda 环境。

```bash
conda activate quant
pip install ib_insync pytz rich duckdb
```

---

### ❌ `Connection refused` / `连接超时`

→ TWS 没开，或者 API 没开启。

**检查清单：**
1. TWS 已登录并正在运行？
2. TWS → Edit → Global Configuration → API → Settings → 已勾选 "Enable ActiveX and Socket Clients"？
3. 端口号是 `7497`（模拟）还是 `7496`（实盘），和 `main.py` 的 `TWS_PORT` 一致？
4. 有没有防火墙拦截本机 7497 端口？

---

### ❌ `找不到合约` / `部分标的连接失败`

→ 合约月份过期了。

打开 `main.py`，把所有 `last_trade_date` 改成 `""` 让程序自动选：

```python
SYMBOLS = [
    SymbolSpec(GC_CONFIG, last_trade_date=""),
    SymbolSpec(ES_CONFIG, last_trade_date=""),
    SymbolSpec(NQ_CONFIG, last_trade_date=""),
]
```

---

### ❌ `RuntimeError: There is no current event loop`

→ Python 版本问题，确保用的是 Python 3.10+：

```bash
python --version   # 应该是 3.10.x 或以上
```

如果不是，重新创建环境：

```bash
conda create -n quant python=3.10 -y
conda activate quant
pip install ib_insync pytz rich duckdb
```

---

### ❌ 界面乱码 / 方块字

→ 终端不支持 Unicode。

- **macOS**：用 iTerm2，或者打开「终端」→ 偏好设置 → 编码选 UTF-8
- **Windows**：打开 PowerShell，输入 `chcp 65001` 切换 UTF-8 编码

---

### ❌ 程序启动了但没数据（界面全是 `---`）

→ 正常，等几秒。程序需要等第一笔 tick 数据到来才开始显示。  
如果超过 1 分钟还没数据，检查 TWS 里对应合约是否有行情订阅权限。

---

## 快速启动备忘卡

把下面这几行贴个便签，以后每次用就这几步：

```bash
# 1. 先开 TWS 并登录
# 2. 打开终端，进入项目父目录
cd /你的项目路径/project
# 3. 启动主程序
conda run -p ~/quant_env python -m intraday.main
# 4. Ctrl+C 退出

# 查询数据（主程序运行中也可以，新开一个终端窗口执行）：
conda run -p ~/quant_env python -m intraday.query --count
```

> 将 `~/quant_env` 替换为你实际的环境路径。

---

## 联系作者

沒事別聯繫！

祝交易顺利！🎯
