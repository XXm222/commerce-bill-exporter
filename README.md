<h1 align="center">电商账单导出器</h1>

<p align="center">
  <a href="#开始使用"><img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&amp;logo=python&amp;logoColor=white"></a>
  <a href="#支持范围"><img alt="支持平台：聚水潭、天猫" src="https://img.shields.io/badge/%E5%B9%B3%E5%8F%B0-%E8%81%9A%E6%B0%B4%E6%BD%AD%20%7C%20%E5%A4%A9%E7%8C%AB-8B5CF6?style=flat-square"></a>
  <a href="#构建桌面程序"><img alt="macOS 和 Windows" src="https://img.shields.io/badge/Desktop-macOS%20%7C%20Windows-0F766E?style=flat-square"></a>
  <a href="#开始使用"><img alt="开发预览" src="https://img.shields.io/badge/%E7%8A%B6%E6%80%81-%E5%BC%80%E5%8F%91%E9%A2%84%E8%A7%88-F59E0B?style=flat-square"></a>
  <a href="LICENSE"><img alt="GPL v3" src="https://img.shields.io/badge/License-GPL--3.0-2E7D32?style=flat-square"></a>
</p>

<p align="center"><strong>选择日期、平台和店铺，按月导出并整理账单。</strong></p>

这是一个 PyQt6 桌面应用，使用电脑上已经安装的 Google Chrome 操作平台页面。目前支持聚水潭和天猫。账号、密码、下载文件和导出记录均留在使用者的电脑上。

> 当前为 `0.6.0-dev` 源码版。平台页面与导出规则可能变化；首次使用时，请将生成文件与平台页面核对。

## 支持范围

| 平台 | 导出内容 | 时间规则 | 文件整理 |
| --- | --- | --- | --- |
| 聚水潭 | 订单商品数据 | 按所选日期查询；跨月时逐月获取 | 一个 Excel 工作簿，每家店铺一个 Sheet |
| 天猫 | 聚核算月度明细、收入账单全量明细 | 将所选日期覆盖的月份逐月导出 | 按店铺和月份保存，每个来源一份工作簿 |

聚水潭包含拼多多店铺时，历史商品明细可能受平台近三个月的导出范围限制。应用会保留已导出的文件，并在结果中提示缺失范围。

## 开始使用

1. 安装 **Python 3.12** 和 **Google Chrome**，克隆本仓库。
2. 按下方命令安装依赖并启动应用。首次启动时，应用会检查浏览器运行环境，并引导安装所需的 [Kimi 浏览器扩展与运行端](https://www.kimi.com/help/kimi-webbridge/kimi-webbridge-introduction)。应用不会附带或另装一份浏览器。
3. 在应用中添加平台账号或店铺，选择日期与要导出的平台、店铺，然后点击「开始导出」。账号密码可保存在本机；如果平台要求滑块、短信等验证，仍需在 Chrome 中完成。

**macOS**

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python app.py
```

**Windows（PowerShell）**

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.venv\Scripts\python.exe app.py
```

导出目录由应用界面选择。文件按平台归档；同一平台的不同账号会分开放置，避免同名店铺互相覆盖。

## 构建桌面程序

在仓库根目录构建 macOS 应用：

```bash
.venv/bin/python -m PyInstaller --noconfirm --clean CommerceBill.spec
```

Windows 请在 PowerShell 中运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\打包-Windows.ps1
```

Windows 脚本会准备虚拟环境、构建程序并运行安装自检。已安装 Inno Setup 6.3 或更高版本时，还会生成 Setup 安装包；否则可使用 `dist/CommerceBill/` 中的程序目录。构建产物不会提交到本仓库。

## 数据与隐私

- 密码由当前系统用户加密保存：macOS 使用钥匙串，Windows 使用 DPAPI。
- 本仓库不包含账号配置、真实账单、浏览器扩展、运行端或安装包。
- 请勿将导出的账单、账号文件或包含真实订单信息的日志提交到 Git。

## 项目结构

```text
app.py                  桌面界面与程序入口
engine.py               导出任务、恢复与文件归档
jst_export.py           聚水潭导出适配
tmall_export.py          天猫导出适配
webbridge.py            浏览器操作接口
environment.py          Chrome 与扩展运行环境检查
CommerceBill.spec       PyInstaller 构建配置
打包-Windows.ps1         Windows 构建脚本
installer.iss           Inno Setup 安装包配置
```

## 许可

本项目依据 [GNU GPL v3.0](LICENSE) 发布。第三方组件遵循各自的许可协议。
