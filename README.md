# 一站 AI 工作台

论文、视频、任务、AI 用量，一站管理。

面向个人长期使用的本地网页工作台。前端为原生 JavaScript 模块，后端为 Python 本机服务。

包含论文与 Obsidian PDF、视频收藏、每日待办、健康记录、社交复盘、记账、推文收藏、本机聊天历史和 Token 统计。读书模块仍在规划中。

## 本机运行

使用 Python 3.11 或以上版本。基础服务主要使用标准库；需要 PDF 预览、压缩日志或推文书签导入时，可安装可选依赖：

```powershell
python -m pip install -r requirements.txt
python server.py
```

在浏览器打开 `http://127.0.0.1:5173/`。Windows 用户也可以使用项目的启动脚本；脚本默认使用 Chrome 的 Default 配置文件。截图验证应使用单独的测试环境。

程序默认只监听 `127.0.0.1`。它会读取当前机器上相关工具的历史和 Obsidian 配置；运行时看到的个人内容并不属于开源源码。不要将本机服务反向代理、内网穿透或部署为公网服务。

## 数据位置

- 视频、健康、社交、记账及论文偏好位于 `%LOCALAPPDATA%/AIWorkbench/`；未设置该变量时使用用户主目录下的 `AIWorkbench/`。
- 聊天与统计读取本机 Codex、Claude Code 等工具的用户目录，部分统计缓存写入项目 `.cache/`。
- 待办、推文收藏、手动论文记录及部分界面偏好使用浏览器 `localStorage`。不同浏览器、配置文件和 origin 之间不共享这些记录。

发布源码不需要导出以上任何数据。软件会按模块功能访问上游论文和视频等服务，不能将“本地存储”理解为完全不联网。

## 检查和发布准备

```powershell
python scripts/check-workbench.py --scope all
python -m unittest discover -s tests -p test_open_source_export.py
python -m unittest discover -s tests -p test_static_privacy.py
python scripts/export-open-source.py
```

发布准备使用逐文件允许清单，导出新的 `.release/` 源码快照及 ZIP，不包含现有 Git 历史或个人数据。具体步骤见 [开源发布说明](docs/OPEN_SOURCE.md)，本次审查范围见 [隐私审查](docs/PRIVACY_AUDIT.md)。

## 许可证

项目源码使用 [MIT License](LICENSE)，署名为 AI Workbench Contributors。运行时导入的第三方内容和用户个人数据不属于源码许可范围；依赖包遵循各自许可证。推文模块从空列表开始，不随源码分发第三方推文全文或示例媒体。
