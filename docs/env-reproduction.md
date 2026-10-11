# 环境核验与第二台设备复现记录

版本：V1｜记录人：C｜记录时间：2026-10-10 00:48～01:30（北京时间）
对应任务：成员C指南 C00（检查运行设备与 Python 环境）、C01（在第二台设备复现并写 README）、
总纲 §9「10/10 12:00 第二台设备启动、README 初稿、测试骨架」。

> **本文件的定位**：C00 要求「检查三台电脑的 Python 环境」，C01 要求「在第二台设备复现」。
> 下面第 1～4 节是**实际执行并留下输出**的部分；第 6 节是需要 A、B 用自己电脑填写的检查表，
> 尚未填写的地方一律写「未核验」，不替队友补结果。

---

## 1. 复核设备

| 设备编号 | 归属 | 用途 | 状态 |
| --- | --- | --- | --- |
| DEV-C | C 的开发机 | 写测试、跑冒烟、组织试用、录屏 | **已核验**（本文件第 2～5 节） |
| DEV-A | A 的开发机 | 后端开发、真实模型调用 | **未核验**（第 6 节待填） |
| DEV-B | B 的开发机 | 页面开发、手机端联调 | **未核验**（第 6 节待填） |

C01 的「第二台设备」在本轮只能用 DEV-C 完成一次**干净虚拟环境复现**；
真正意义上的第二台设备（DEV-A 或 DEV-B）尚未参与，见第 6 节与第 7 节的风险说明。

---

## 2. 解释器核验

契约要求 **Python 3.11**（总纲 §3.1），且三人版本一致。

在本机执行 `py -0p` 与 `python --version` 的结果：

| 解释器 | 路径 | 版本 | 可用性 |
| --- | --- | --- | --- |
| `py -3.13`（默认） | `D:\python\python.exe` | **3.13.7** | 可用，本轮实际使用 |
| `py -3.9` | `%LOCALAPPDATA%\Programs\Python\Python39\python.exe` | 3.9.x | 可用，但不满足依赖下限 |
| `python`（PATH 首位） | `C:\Users\MATEBOOK\miniconda3\python.exe` | **3.8.18** | 可用，但过旧，**不要用** |
| 3.11 | —— | **不存在** | `py -3.11` 报 “No suitable Python runtime found” |

**结论（需要团队决定）**：本机没有 Python 3.11。

- 方案一（推荐）：团队成员各自安装 Python 3.11，按 README 第 1 节重建虚拟环境，
  再重跑核心用例；这是与总纲一致的做法。
- 方案二：团队当天一起确认把契约改为「3.11 及以上」，并把实际版本写进 README 与
  `requirements.txt` 备注；**必须三人一致后再改**，不能由 C 单方面改契约。

本轮按方案二的方式先跑通，但**没有把契约改成 3.11+**，契约仍以 3.11 为准。

---

## 3. 干净虚拟环境复现（C01）

在仓库根目录执行（Windows PowerShell）：

```powershell
# 契约写法（本机因缺 3.11 未能执行）：
#   py -3.11 -m venv .venv
D:\python\python.exe -m venv .venv          # 实际：3.13.7
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip install -r requirements-c-repro.txt
```

实测结果：

| 检查项 | 结果 |
| --- | --- |
| 虚拟环境创建 | 成功，`sys.prefix` 指向仓库内 `.venv` |
| 解释器版本 | `Python 3.13.7`（契约要求 3.11，见第 2 节） |
| 依赖安装 | 成功；实际版本见 [`requirements-c-repro.txt`](../requirements-c-repro.txt) |
| `python -m uvicorn app.main:app` | **未通过** —— `app/main.py` 尚不存在（A01 未交付） |
| `GET /health` 返回 200 | **未测** —— 服务无法启动，T13 的「启动」部分因此未完成 |
| `python -m pytest --collect-only` | 成功，收集 61 个用例（10 个脚手架自检 + 51 个契约用例） |
| `python -m pytest tests/test_harness.py -q` | **10 passed** |
| `python -m pytest tests/test_api.py tests/test_statistics.py tests/test_workflow.py -q` | 51 skipped，原因统一为「找不到 app.main」 |
| `python scripts/smoke.py` | 退出码 3（服务不可达），提示信息可读 |

**结论**：`DATABASE_PATH`、依赖安装、测试收集与脚手架这几项已经独立复现；
**「按 README 启动服务并看到 /health 200」这一条 C01 验收未完成**，因为 A 的后端还没交付。
A 交付 `app/` 后需要立刻补做这一步，见 [`handoff-c-2026-10-09-10.md`](handoff-c-2026-10-09-10.md)。

---

## 4. 本机发现的环境缺陷（不是代码问题）

### 4.1 现象

在 Windows + DSH 文件沙箱下，`python -m venv`（内嵌 ensurepip）与 `pip install` 会失败：

```
PermissionError: [Errno 13] Permission denied:
  ...\firstorder-lab\.tmp\tmppkgxk4r2\pip-25.2-py3-none-any.whl
```

### 4.2 定位过程（可复现）

用 `os.mkdir` 的不同 mode 建目录后立刻写入文件，结果如下：

| 调用 | 结果 |
| --- | --- |
| `os.mkdir(d)`（默认 0o777） | 写入 OK |
| `os.mkdir(d, 0o755)` | 写入 OK |
| `os.mkdir(d, 0o700)` | **PermissionError** |
| `os.makedirs(d)`（默认 mode） | 写入 OK |
| `tempfile.mkdtemp(dir=...)`（内部用 0o700） | **PermissionError** |

`icacls` 显示沙箱在工作区目录上添加了受控授权项，而 0o700 产生的仅所有者权限
让沙箱无法在新目录上落地授权，于是任何新建临时目录都不可写。

**影响面**：`pip`、`ensurepip`、`pytest` 的 `tmp_path`（内部用 `mkdtemp`）全部受影响。
这是**受限沙箱环境**与 CPython 在 Windows 上 mode→权限映射的交互问题，不是仓库代码的缺陷。

> ⚠️ **重要区分：这只影响「被沙箱限制的自动执行环境」，不影响人自己打开的终端。**
> 我们自己在 Windows 上双击打开 PowerShell、`cd` 到项目里执行同样的命令时，新目录的所有者
> 就是当前用户，写入正常。**A、B 不需要做这一节的任何操作**，也不需要 `tools/sitecustomize.py`。

### 4.3 规避方式（仅受限环境使用，**不进入交付物**）

`tools/sitecustomize.py` 是本机为了在受限环境里跑通 `pip`/`pytest` 临时写的垫片：
当该目录在 `PYTHONPATH` 上时，Python 启动会导入它，把「无 group/other 位」的 mode 放宽为
0o777。它不提升任何权限、不修改机器上的权限设置，只对显式设置 `PYTHONPATH` 的命令生效。

**它不属于项目代码，不要提交到仓库**（见 `handoff-c-2026-10-09-10.md` 的上传清单）。
正常终端里跑测试完全不需要它：

```powershell
python -m pytest tests/test_api.py tests/test_statistics.py tests/test_workflow.py -q
```

只有在受限环境里才需要下面这套额外设置：

```powershell
$root = "C:\Users\MATEBOOK\Documents\deepseek-harness\default-workspace\firstorder-lab"
$env:PYTHONPATH = "$root\tools"
$env:TMP = "$root\.localtmp"; $env:TEMP = "$root\.localtmp"   # 避开历史坏目录
& "$root\.venv\Scripts\python.exe" -m pytest -q --basetemp "$root\.localtmp\pytest"
```

> 注意：一旦某次 pytest 在坏权限的临时根下创建过 `pytest-of-<用户>` 目录，
> 之后不换 `TMP/TEMP` 就会一直失败。本轮已改用仓库内 `.localtmp`。

### 4.4 正常终端里不需要

A、B（以及我们自己在普通 PowerShell 窗口里）按 README 第 1、2 节正常操作即可。
如果谁在**没有**这层沙箱的电脑上仍然遇到同样的 `PermissionError`，
请把上面的定位表原样贴回来 —— 那就是另一类问题，需要重新定位。

---

## 5. 网络与依赖源

| 项目 | 结果 |
| --- | --- |
| `pypi.org` 直连 | 不通（TLS 握手被中断） |
| `pypi.tuna.tsinghua.edu.cn` | 可用；本机 `pip config` 的 `global.index-url` 已指向该镜像 |
| 安装要求 | 使用 `--only-binary :all:` 可显著缩短解析时间；不加时 pip 解析会长期占用 CPU |

结论：依赖安装走清华镜像，命令与版本已记入 `requirements-c-repro.txt`。

---

## 6. 三台设备的检查表（DEV-A / DEV-B 待填）

请 A、B 在自己的电脑上执行并把结果贴回团队同步：

```powershell
py -0p                       # 列出所有已安装的解释器
python --version             # 默认解释器版本
python -m venv .venv         # 能否创建虚拟环境
.\.venv\Scripts\python.exe -m pip --version
```

| 检查项 | DEV-A | DEV-B |
| --- | --- | --- |
| 操作系统与版本 | 未核验 | 未核验 |
| 是否有 Python 3.11 | 未核验 | 未核验 |
| 默认解释器版本 | 未核验 | 未核验 |
| 能否创建 venv | 未核验 | 未核验 |
| 能否安装依赖 | 未核验 | 未核验 |
| 能否按 README 启动服务 | 未核验 | 未核验 |

---

## 7. 结论与待办

- [x] 本机解释器清单、依赖安装、测试收集与脚手架自检已复现并留证；
- [x] 定位并规避了本机沙箱的 `mkdtemp` 权限缺陷；
- [ ] **T13 的「第二台设备按 README 启动」未完成**：需要 A 交付 `app/` 后，在另一台设备上执行；
- [ ] Python 版本需要团队决定（装 3.11，还是三人一起把契约改为 3.11+）；
- [ ] DEV-A / DEV-B 的检查表待 A、B 填写；
- [ ] A 交付 `app/` 后立即补做：`uvicorn app.main:app` 启动 + `/health` 200 + 核心用例实跑。
