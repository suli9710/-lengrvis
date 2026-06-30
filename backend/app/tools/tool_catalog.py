"""Central catalog of tool descriptions and search hints.

Registration sites look up these entries so the planner prompt and the
tool.search ranking see meaningful text instead of generated placeholders.
Search hints mix English and Chinese synonyms because user goals arrive in
both languages. Keep descriptions to one sentence; risk/approval semantics
belong to the risk-level system, not here.
"""

from __future__ import annotations

# name -> (description, search_hint)
TOOL_CATALOG: dict[str, tuple[str, str]] = {
    # --- file tools ---
    "file.search_by_name": (
        "Search files by name pattern under authorized directories and return matching paths",
        "search find name filename pattern locate 搜索 查找 文件名 匹配 定位",
    ),
    "file.search_full_text": (
        "Search text content across files in authorized directories and return paths with snippets",
        "search text content full-text grep keyword 搜索 文本 内容 全文 关键词",
    ),
    "file.semantic_search": (
        "Search files by meaning using vector embeddings to find conceptually similar documents",
        "search semantic embedding similarity meaning 搜索 语义 相似 含义",
    ),
    "file.list_directory": (
        "List files and subdirectories of a directory path with basic metadata",
        "list directory folder contents entries 列出 目录 文件夹 内容",
    ),
    "file.get_metadata": (
        "Return file metadata including size, timestamps, extension, and content hash",
        "metadata info properties size timestamp hash 元数据 属性 大小 时间",
    ),
    "file.hash_file": (
        "Compute the SHA-256 hash of a file for integrity checks and deduplication",
        "hash checksum sha256 integrity verify 哈希 校验 完整性",
    ),
    "file.read_text": (
        "Read text content from a file with an optional character limit",
        "read text content open view 读取 文本 内容 查看",
    ),
    "file.find_duplicates": (
        "Find duplicate files by hashing contents and grouping identical files",
        "duplicates dedupe same identical copies 重复 去重 相同 副本",
    ),
    "file.cleanup_scan": (
        "Scan authorized directories for cleanup candidates such as large, old, or temporary files",
        "cleanup scan analyze large temp old 清理 扫描 大文件 临时",
    ),
    "file.cleanup_plan": (
        "Build a reviewable cleanup plan listing files to remove and the space to reclaim",
        "cleanup plan proposal preview space 清理 计划 方案 预览 空间",
    ),
    "file.dedupe_plan": (
        "Build a reviewable deduplication plan that keeps one copy per duplicate group",
        "dedupe plan duplicates removal keep 去重 计划 重复 保留",
    ),
    "file.cleanup_execute": (
        "Execute an approved cleanup plan and move the listed files to the Recycle Bin",
        "cleanup execute delete remove apply 清理 执行 删除 应用",
    ),
    "file.cleanup_rollback": (
        "Roll back a previous cleanup execution and restore the affected files",
        "rollback undo restore recover cleanup 回滚 撤销 恢复",
    ),
    "file.preview_batch_operation": (
        "Preview a batch file operation (copy, move, rename) without applying changes",
        "preview batch operation dry-run simulate 预览 批量 操作 模拟",
    ),
    "file.create_folder": (
        "Create a new folder at the given path",
        "create folder directory mkdir new 创建 文件夹 目录 新建",
    ),
    "file.copy": (
        "Copy a file or folder from a source path to a destination path",
        "copy duplicate clone source destination 复制 拷贝 来源 目标",
    ),
    "file.move": (
        "Move a file or folder from a source path to a destination path",
        "move relocate transfer source destination 移动 转移 来源 目标",
    ),
    "file.rename": (
        "Rename a file or folder in place to a new name",
        "rename change name 重命名 改名 名称",
    ),
    "file.trash": (
        "Move a file or folder to the Windows Recycle Bin instead of deleting permanently",
        "trash delete remove recycle bin 删除 移除 回收站 垃圾箱",
    ),
    "file.write_text": (
        "Write text content to a file, creating it if needed",
        "write text create save content 写入 文本 创建 保存",
    ),
    "file.edit_text": (
        "Replace a text string in a file with a new string, optionally replacing all occurrences",
        "edit replace text string modify 编辑 替换 文本 修改",
    ),
    "file.generate_markdown_report": (
        "Write a markdown-formatted report document to a file path",
        "generate markdown report write document 生成 报告 写入 文档",
    ),
    # --- cluster tools ---
    "file.cluster": (
        "Group files into clusters by name and extension similarity",
        "cluster group organize files category 分组 聚类 整理 文件 分类",
    ),
    "file.cluster_by_content": (
        "Group files into semantic clusters based on filename and content similarity",
        "cluster group organize content semantic 分组 聚类 内容 语义",
    ),
    "app.cluster_installed": (
        "Categorize installed applications into groups such as development, media, office, and browsers",
        "cluster apps applications category group 分组 应用 分类 软件",
    ),
    "image.cluster": (
        "Cluster images by scene, people, time, location, objects, or a custom dimension",
        "cluster images photos scene people time location 分组 图片 照片 场景 人物 时间",
    ),
    "image.cluster_images": (
        "Cluster images by scene, people, time, location, objects, or a custom dimension",
        "cluster images photos scene people time location 分组 图片 照片 场景 人物",
    ),
    "file.suggest_folder_structure": (
        "Suggest a folder organization structure based on file types and categories",
        "suggest folder structure organize tidy 建议 文件夹 结构 整理",
    ),
    # --- system tools ---
    "system.get_info": (
        "Return system information including platform, CPU, memory, and processor details",
        "system info platform cpu memory hardware 系统 信息 硬件 处理器 内存",
    ),
    "system.get_disks": (
        "List disk drives with partition, filesystem, and usage statistics",
        "disk drive partition usage space storage 磁盘 分区 空间 存储 容量",
    ),
    "system.get_network": (
        "List network interfaces and their IP address configuration",
        "network interface ip address adapter 网络 接口 地址 网卡",
    ),
    "system.get_battery": (
        "Report battery charge percentage and remaining time when available",
        "battery power charge percentage 电池 电量 充电 百分比",
    ),
    "system.get_startup_items": (
        "List Windows startup programs from the registry and Startup folders",
        "startup boot autorun programs registry 启动项 开机 自启动 程序",
    ),
    "system.open_settings_uri": (
        "Open the Windows Settings app, optionally at a specific ms-settings page",
        "settings open windows ms-settings control panel 设置 打开 控制面板",
    ),
    "system.find_large_files": (
        "Find files exceeding a size threshold in authorized directories",
        "large files size threshold space 大文件 大小 占用 空间",
    ),
    "system.cleanup_suggestions": (
        "Suggest cleanup actions for temporary files, caches, and large files",
        "cleanup suggestions temp cache recommend 清理 建议 临时 缓存",
    ),
    "system.get_processes": (
        "List running processes with PID, memory usage, and CPU percentage",
        "process pid memory cpu running task 进程 内存 任务 运行",
    ),
    "system.local_ai_status": (
        "Check local AI runtime readiness including ONNX, Ollama, and model availability",
        "local ai status onnx ollama model 本地 模型 状态 就绪",
    ),
    "system.diagnostics": (
        "Run a read-only system health check covering CPU, memory, disk, network, and local AI",
        "diagnostics health check status report 诊断 体检 检查 健康 状态",
    ),
    # --- app tools ---
    "app.list_installed": (
        "List installed applications from the registry, shortcuts, and the allowlist",
        "list installed apps applications software programs 列出 已安装 应用 软件 程序",
    ),
    "app.launch_allowlisted": (
        "Launch an application by name from the built-in allowlist",
        "launch run start open app allowlisted 启动 运行 打开 应用",
    ),
    "app.launch_installed": (
        "Launch an installed application by name when it matches the allowlist policy",
        "launch run start open installed app 启动 运行 打开 已安装 应用",
    ),
    "app.find_uninstall_entries": (
        "Find uninstall entries matching an application query in the registry",
        "find uninstall entries app registry lookup 查找 卸载 条目 应用",
    ),
    "app.uninstall_app": (
        "Launch the vendor uninstaller for the application matching the query",
        "uninstall remove app software program 卸载 删除 移除 程序 软件",
    ),
    "app.open_file": (
        "Open a file with its default application",
        "open file launch default view 打开 文件 查看 默认程序",
    ),
    "app.open_folder": (
        "Open a folder in Windows Explorer",
        "open folder directory explorer browse 打开 文件夹 目录 资源管理器",
    ),
    "app.reveal_in_explorer": (
        "Reveal a file or folder in Windows Explorer at its location",
        "reveal show locate explorer select 显示 定位 资源管理器",
    ),
    # --- excel tools ---
    "app.excel.status": (
        "Check Excel COM automation availability and list allowed workbook operations",
        "excel status com availability check 状态 可用 检查 表格",
    ),
    "app.excel.read_workbook_summary": (
        "Summarize a workbook's structure with sheet names, dimensions, and a cell preview",
        "excel read workbook summary sheets preview 读取 工作簿 摘要 表格 预览",
    ),
    "app.excel.write_cell": (
        "Write a value to a single cell in an Excel workbook and save it",
        "excel write cell value modify update 写入 单元格 修改 表格",
    ),
    # --- document tools ---
    "document.extract_text": (
        "Extract plain text from a document (pdf, docx, xlsx, pptx, txt, csv)",
        "extract text document pdf word read 提取 文本 文档 读取",
    ),
    "document.summarize": (
        "Summarize a document's content into a concise overview",
        "summarize document summary abstract digest 总结 摘要 文档 概要",
    ),
    "document.qa": (
        "Answer a question about a document's content",
        "question answer document ask qa 问答 提问 文档 回答",
    ),
    "document.convert_to_markdown": (
        "Convert a document's content to markdown format",
        "convert markdown document format 转换 格式 文档",
    ),
    "document.analyze_csv": (
        "Analyze a CSV file's structure including row count and column names",
        "analyze csv columns rows data table 分析 表格 数据 列",
    ),
    "document.analyze_xlsx": (
        "Analyze and preview an Excel workbook's content and structure",
        "analyze excel xlsx workbook preview 分析 工作簿 表格 预览",
    ),
    "document.generate_report": (
        "Generate a formatted report from text content with an optional title",
        "generate report document write 生成 报告 文档",
    ),
    "document.parse_advanced": (
        "Parse a document with the document-intelligence service for detailed structure analysis",
        "parse advanced document structure analysis 解析 文档 结构 分析",
    ),
    "document.extract_tables": (
        "Extract tables from a document into structured data",
        "extract tables table structured data 提取 表格 结构化 数据",
    ),
    "document.ask_with_citations": (
        "Answer a question about a document with source citations",
        "ask question citations sources references 提问 引用 来源 出处",
    ),
    "document.compare": (
        "Compare two documents and report differences and similarities",
        "compare documents difference similarity diff 比较 文档 差异 相似",
    ),
    "document.redact_preview": (
        "Preview a document with sensitive data redaction patterns applied",
        "redact preview sensitive privacy mask 脱敏 预览 隐私 敏感",
    ),
    "document.apply_redaction": (
        "Apply redaction patterns to a document after preview approval",
        "apply redaction sensitive privacy mask write 脱敏 写入 应用 隐私",
    ),
    "document.edit_docx": (
        (
            "Find and replace text inside a Word docx document (headings, body, tables); "
            "dry-run preview then write with rollback backup"
        ),
        "edit docx word replace find modify heading title 编辑 替换 文档 word 标题",
    ),
    "document.edit_xlsx": (
        "Update a single cell value in an Excel xlsx workbook; dry-run preview then write with rollback backup",
        "edit xlsx excel cell value modify 编辑 单元格 表格 excel",
    ),
    "document.edit_pptx": (
        "Find and replace text inside a PowerPoint pptx deck; dry-run preview then write with rollback backup",
        "edit pptx powerpoint slide replace find modify 编辑 替换 幻灯片 ppt",
    ),
    "document.generate_cited_report": (
        "Generate a report answering a query over documents with inline citations",
        "generate report cited citations query 生成 报告 引用 查询",
    ),
    # --- browser tools ---
    "browser.session_start": (
        "Start a managed browser session at a URL and return the session id",
        "session start browser begin open 会话 启动 浏览器 开始",
    ),
    "browser.session_close": (
        "Close an active browser session by session id",
        "session close end stop browser 会话 关闭 结束 浏览器",
    ),
    "browser.session_info": (
        "Return status and metadata for a browser session",
        "session info status metadata 会话 信息 状态",
    ),
    "browser.sessions": (
        "List all active browser sessions",
        "sessions list active browser 会话 列表 浏览器",
    ),
    "browser.events": (
        "Return the recorded event log for a browser session",
        "events log activity history 事件 日志 历史 记录",
    ),
    "browser.observe": (
        "Observe the current page and return its text, title, URL, and links",
        "observe inspect page state read 观察 检查 页面 状态",
    ),
    "browser.act": (
        "Perform a browser action such as click, fill, or navigate in a session",
        "act action click fill navigate perform 执行 操作 点击 填写",
    ),
    "browser.cua_run": (
        "Run a natural-language instruction through the computer-use provider for browser automation",
        "cua automation instruction natural language 自动化 指令 自然语言",
    ),
    "browser.cua": (
        "Run a natural-language instruction through the computer-use provider for browser automation",
        "cua automation instruction agent 自动化 指令 代理",
    ),
    "browser.replay_export": (
        "Export a browser session's recorded activity as a replay file",
        "replay export recording save 回放 导出 录制 保存",
    ),
    "browser.open_url": (
        "Open a URL in the managed browser and return the normalized URL",
        "open url visit link navigate website 打开 链接 访问 网址 网站",
    ),
    "browser.read_page": (
        "Read a page's text, title, and links from a URL with an optional character limit",
        "read page content text fetch 读取 页面 内容 网页",
    ),
    "browser.summarize_page": (
        "Read a page and return a short text summary of its content",
        "summarize page content brief 总结 页面 摘要 网页",
    ),
    "browser.screenshot": (
        "Capture a screenshot of a page and return the image path",
        "screenshot capture image snapshot 截图 截屏 快照 网页",
    ),
    "browser.search_web_via_provider": (
        "Search the web via the configured provider and return top result links",
        "search web query results internet 搜索 网络 查询 互联网 上网",
    ),
    "browser.extract_links": (
        "Extract hyperlinks from a page and return the link list",
        "extract links hyperlinks urls 提取 链接 网址",
    ),
    "browser.navigate": (
        "Navigate the current browser session to a URL",
        "navigate go url page visit 导航 跳转 访问 页面",
    ),
    "browser.wait_for_selector": (
        "Wait for a DOM element matching a selector to appear within a timeout",
        "wait selector element appear timeout 等待 元素 选择器 出现",
    ),
    "browser.click_element": (
        "Click a page element identified by a CSS selector",
        "click element button selector press 点击 按钮 元素 选择器",
    ),
    "browser.fill_form": (
        "Fill form fields on a page with the provided non-sensitive values",
        "fill form fields input values 填写 表单 输入 字段",
    ),
    "browser.submit_form": (
        "Submit a form identified by a CSS selector",
        "submit form send post 提交 表单 发送",
    ),
    # --- search tools ---
    "search.query": (
        "Search the web and return top results with titles and URLs",
        "search query web results internet lookup 搜索 查询 网络 检索 上网",
    ),
    "search.fetch_result": (
        "Fetch the full content of a search-result URL and return its text and links",
        "fetch result content url page open 获取 内容 页面 网页 抓取",
    ),
    "search.summarize_results": (
        "Search the web and summarize the top results as a short list",
        "summarize search results overview 总结 搜索 结果 概览",
    ),
    # --- vision tools ---
    "vision.describe_image": (
        "Describe an image with a vision model and return description, tags, and labels",
        "describe image vision recognize tags 描述 图片 识别 标签 看图",
    ),
    "vision.describe_images": (
        "Describe multiple images from paths or a directory and return all descriptions",
        "describe images batch multiple vision 描述 图片 批量 多张",
    ),
    "vision.ocr_image": (
        "Extract text from an image using OCR",
        "ocr text extract image recognize 文字识别 提取 图片 文本",
    ),
    "vision.embed_image": (
        "Generate a vector embedding for an image",
        "embed image vector embedding 嵌入 向量 图片",
    ),
    "vision.compare_images": (
        "Compare two images and return their descriptions and a similarity score",
        "compare images similarity difference 比较 图片 相似 差异",
    ),
    # --- remote tools ---
    "remote.view_screen": (
        "Capture a screenshot of the desktop for remote viewing",
        "view screen screenshot desktop remote 查看 屏幕 截图 远程 桌面",
    ),
    "remote.click": (
        "Click at x,y coordinates on the desktop for remote control",
        "click mouse coordinates remote 点击 鼠标 坐标 远程",
    ),
    "remote.type_text": (
        "Type text into the focused window for remote control",
        "type text input keyboard remote 输入 文本 键盘 远程",
    ),
    "remote.key_press": (
        "Press a keyboard key such as enter, space, or backspace for remote control",
        "key press keyboard enter 按键 键盘 回车",
    ),
    # --- developer tools ---
    "dev.glob": (
        "Match files recursively by glob pattern within the authorized root",
        "glob pattern files match find 文件 匹配 模式 查找",
    ),
    "dev.grep": (
        "Search file contents for a text query with an optional file pattern filter",
        "grep search content code text 搜索 内容 代码 文本",
    ),
    "dev.git_status": (
        "Show the current git status including branch and changed files",
        "git status branch changes 状态 分支 变更 代码",
    ),
    "dev.diff_preview": (
        "Show the unstaged git diff with an optional pathspec filter",
        "diff git changes patch 差异 变更 补丁 代码",
    ),
    "dev.shell_readonly": (
        "Run a read-only shell command from the allowlisted inspection set",
        "shell readonly command inspect 命令 只读 检查",
    ),
    "dev.pytest_inventory": (
        "Scan test files and list pytest test functions statically",
        "pytest tests inventory list 测试 清单 列表",
    ),
    "dev.worktree_preview": (
        "Preview the git worktree commands for creating or removing an isolated branch",
        "worktree git branch isolated preview 工作树 分支 隔离 预览",
    ),
    "dev.test_run": (
        "Run a controlled test command (pytest, npm, python) in the foreground or background",
        "test run pytest npm execute 测试 运行 执行",
    ),
    "dev.test_status": (
        "Check the status of a background test run by task id",
        "test status background task 测试 状态 后台 任务",
    ),
}


def tool_description(name: str) -> str:
    """Catalog description for a tool, falling back to a readable name."""
    entry = TOOL_CATALOG.get(name)
    return entry[0] if entry else name.replace(".", " ")


def tool_search_hint(name: str) -> str:
    """Catalog search hint for a tool, empty when uncatalogued."""
    entry = TOOL_CATALOG.get(name)
    return entry[1] if entry else ""
