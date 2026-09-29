# 电商账单

一个基于 PyQt6 的桌面账单导出工具。目前接入聚水潭和天猫：在界面中选择日期、平台与账号，使用本机已安装的 Google Chrome 完成浏览器操作，并将导出结果按平台、店铺整理。聚水潭导出订单商品数据；天猫按整月处理资金管理明细与收支账单。

> 当前源码版本为 **0.6.0-dev**。平台页面、账号权限和导出规则会变化；正式使用前请用自己的账号核对导出文件与平台页面。此仓库不包含浏览器、浏览器扩展、运行端、安装包或任何账号与账单数据。

## 运行

需要 Python 3.12、Google Chrome 和 PyQt6。浏览器操作使用 [Kimi 浏览器扩展及本机运行端](https://www.kimi.com/help/kimi-webbridge/kimi-webbridge-introduction)；APP 会检查运行环境，缺少时给出安装入口。请按官方说明配置扩展和运行端，在 Chrome 中登录自己的平台账号。

macOS 开发环境：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r 源码/requirements-build.txt
.venv/bin/python 源码/app.py
```

Windows 开发环境：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r 源码\requirements-build.txt
.venv\Scripts\python.exe 源码\app.py
```

运行自检：

```bash
.venv/bin/python 源码/app.py --self-test self-test.json
```

Windows 下将上面的解释器路径替换为 `.venv\Scripts\python.exe`。自检只检查本机环境和程序组件，不能代替真实平台导出验收。

## 打包

macOS 可在 `源码` 目录运行 `pyinstaller CommerceBill.spec`。Windows 使用 `源码/打包-Windows.ps1`；它会建立虚拟环境、运行 PyInstaller、自检，并在安装了 Inno Setup 6.3+ 时编译安装包。打包脚本和安装配置均在仓库中，生成的二进制文件不纳入 Git。

## 测试

仓库只收录不需要真实账号或账单的离线测试：

```bash
PYTHONPATH=源码 .venv/bin/python -m unittest discover -s 测试 -p 'test_*.py'
```

Windows 可将 `PYTHONPATH=源码` 改为 `$env:PYTHONPATH='源码'`，并使用虚拟环境里的 `python.exe`。

## 数据与安全

账号信息保存在用户本机；macOS 密码使用系统钥匙串，Windows 密码使用当前用户的 DPAPI 加密。不要将账号配置、测试店铺清单、导出账单或含真实订单号的日志提交到仓库。Chrome 扩展和本机运行端是独立项目，本仓库不分发其代码或二进制文件。

## 许可

本项目源码按 **GNU GPL v3.0** 发布。PyQt6 开源版本本身采用 GPL v3；请遵守项目及第三方依赖各自的许可。见 [LICENSE](LICENSE)。
