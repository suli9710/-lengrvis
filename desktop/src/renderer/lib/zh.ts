import type {
  AuditLogEntry,
  BackendState,
} from "../../shared/types";
import type { ChatRole } from "../../shared/catalogTypes";
import type {
  AgentConversation,
  AgentMessage,
  ApprovalRequest,
  PlanStepState,
  SafetyFinding,
  SafetyReview,
  SafetySeverity,
  TaskState
} from "../../shared/executionTypes";

export function zhBackendState(state: BackendState) {
  const labels: Record<BackendState, string> = {
    not_configured: "未配置",
    starting: "启动中",
    running: "运行中",
    stopped: "已停止",
    error: "异常"
  };
  return labels[state] ?? state;
}

export function zhConnectionState(state: "online" | "offline" | "checking") {
  return {
    online: "在线",
    offline: "离线",
    checking: "检查中"
  }[state];
}

export function zhTaskState(state: TaskState) {
  const labels: Record<TaskState, string> = {
    queued: "排队中",
    running: "执行中",
    blocked: "待审批",
    paused: "已暂停",
    completed: "已完成",
    failed: "失败",
    denied: "已拒绝",
    cancelled: "已取消",
    rolled_back: "已回滚",
    repair_required: "需要修复"
  };
  return labels[state] ?? state;
}

export function zhBackendTaskStatus(status?: string) {
  if (!status) return "未知";
  const normalized = status.trim().toLowerCase();
  const labels: Record<string, string> = {
    created: "已创建",
    goal_analysis: "分析目标中",
    queued: "排队中",
    planning: "规划中",
    consultation: "Agent 协作中",
    plan_review: "审核计划中",
    execution: "执行中",
    final_review: "核验结果中",
    rolled_back: "已回滚",
    repair_required: "回滚后需要修复",
    // Legacy transport aliases remain readable while older evidence is open.
    agent_consultation: "Agent 协作中",
    reviewing_plan: "审核计划中",
    reviewing_tool_call: "审核工具调用中",
    executing_tool: "执行工具中",
    waiting_user_approval: "等待用户审批",
    completed: "已完成",
    failed: "失败",
    denied: "已拒绝",
    cancelled: "已取消",
    paused: "已暂停"
  };
  return labels[normalized] ?? status;
}

export function zhStepState(state: PlanStepState) {
  const labels: Record<PlanStepState, string> = {
    pending: "待处理",
    active: "执行中",
    done: "已完成",
    blocked: "受阻"
  };
  return labels[state] ?? state;
}

export function zhConversationStatus(status: AgentConversation["status"]) {
  const labels: Record<AgentConversation["status"], string> = {
    idle: "空闲",
    running: "协作中",
    waiting: "等待中",
    done: "已完成"
  };
  return labels[status] ?? status;
}

export function zhSafetyStatus(status: SafetyReview["status"]) {
  const labels: Record<SafetyReview["status"], string> = {
    clear: "已通过",
    needs_review: "需审批",
    blocked: "已拦截"
  };
  return labels[status] ?? status;
}

export function zhSafetyVerdict(verdict?: string) {
  if (!verdict) return "未知";
  const labels: Record<string, string> = {
    allow: "已允许",
    needs_user_approval: "需要用户审批",
    revise_plan: "需要修改计划",
    deny: "已拒绝"
  };
  return labels[verdict] ?? verdict;
}

export function zhRiskLevel(risk?: string) {
  if (!risk) return "未知风险";
  const labels: Record<string, string> = {
    R0_READ_ONLY: "R0 只读",
    R1_OPEN_ONLY: "R1 打开类操作",
    R2_REVERSIBLE_MODIFY: "R2 可回滚修改",
    R3_DESTRUCTIVE_OR_SYSTEM: "R3 破坏性或系统操作",
    R4_FORBIDDEN_OR_HANDOFF: "R4 禁止或需人工接管"
  };
  return labels[risk] ?? risk;
}

export function zhApprovalType(type?: string) {
  if (!type) return "审批请求";
  const labels: Record<string, string> = {
    tool_call: "工具调用审批",
    file_operation: "文件操作审批",
    cleanup: "清理计划审批",
    cleanup_plan: "清理计划审批",
    cleanup_execute: "执行清理审批",
    system_change: "系统变更审批",
    browser_action: "浏览器操作审批",
    app_launch: "应用启动审批"
  };
  return labels[type] ?? type;
}

export function zhBackendText(text?: string): string {
  if (!text) return "";
  const naturalReply = naturalSupervisorReply(text);
  if (naturalReply) return naturalReply;
  const userFacingError = zhUserFacingError(text);
  if (userFacingError !== text.trim()) return userFacingError;
  const exact: Record<string, string> = {
    "Primary provider failed; using MockProvider fallback:": "AI 服务暂时不可用，已用临时模式继续处理：",
    "Provider returned invalid plan; using MockProvider fallback:": "AI 服务返回的计划不完整，已用临时模式继续处理：",
    "File paths must stay inside authorized directories; modifying steps need dry-run previews.": "为了保护你的文件，只会处理你先选择过的文件夹；移动、重命名或删除前会先给你确认。",
    "System inspection is read-only unless a Windows settings operation is explicitly approved.": "系统检查默认只读；只有明确审批后才允许执行 Windows 设置类操作。",
    "Application operations are limited to indexed apps and authorized file/folder open actions; unknown executables require approval or are blocked.": "应用操作只会打开已识别的应用，以及你先选择过的文件或文件夹；不认识的程序会先停下来确认。",
    "Browser operations start read-only; login, payment, submission, and messaging are handoff-only.": "浏览器操作默认只读；登录、支付、提交表单和发消息都必须交给人工处理。",
    "External search results must preserve source URL, title, summary, and retrieval time.": "外部搜索结果必须保留来源 URL、标题、摘要和检索时间。",
    "SafetyReviewAgent stopped the task during initial runtime supervision.": "安全审核 Agent 在初始运行监督中拦截了任务。",
    "SafetyReviewAgent stopped the task after PlannerAgent output.": "安全审核 Agent 在规划输出后拦截了任务。",
    "SafetyReviewAgent stopped the task before executing a tool call.": "安全审核 Agent 在执行工具调用前拦截了任务。",
    "SafetyReviewAgent stopped the task after observing tool output.": "安全审核 Agent 在观察工具结果后拦截了任务。",
    "Plan generated and waiting for approval on modifying steps.": "计划已生成，修改类步骤正在等待审批。",
    "Task completed with read-only/open-only MVP tools.": "任务已通过只读或打开类工具完成。",
    "Waiting for user approval before executing modifying operation.": "正在等待用户审批，审批后才会执行修改操作。",
    "Preview only. Approval is required before any file is moved, copied, renamed, or deleted.": "当前仅为预览。移动、复制、重命名或删除文件前必须先获得审批。",
    "Explicit absolute path is required when no authorized directories are configured.": "请先选择要整理的文件夹，或填写完整的文件位置，例如桌面、下载、文档、图片里的某个文件。",
    "No authorized directories configured.": "还没有选择要整理的文件夹。请先添加桌面、下载、文档或图片。",
    "Path is outside authorized directories.": "这个文件不在你已选择的文件夹里。请换一个文件，或先把所在文件夹加入设置。",
    "Plan denied.": "计划已被安全策略拦截。",
    "Analyze spreadsheet": "分析表格",
    "Analyze the visible spreadsheet and summarize the important numbers.": "分析当前可见的表格，并总结重要数据。",
    "Spreadsheet context is visible.": "检测到当前窗口包含表格内容。",
    "Summarize document": "总结文档",
    "Summarize the visible document and call out likely next actions.": "总结当前可见的文档，并列出可能的下一步。",
    "Document editing or reading context is visible.": "检测到当前窗口正在阅读或编辑文档。",
    "Read page": "读取网页",
    "Read the current page and extract the useful facts.": "读取当前网页并提取有用信息。",
    "Browser context is visible.": "检测到当前窗口包含网页内容。",
    "Organize files": "整理文件",
    "Review the visible folder and suggest a safe organization plan.": "检查当前可见的文件夹，并建议安全的整理方案。",
    "File or folder context is visible.": "检测到当前窗口包含文件或文件夹。",
    "Check system": "检查系统",
    "Check the visible system state and suggest a read-only diagnostic next step.": "检查当前可见的系统状态，并建议只读诊断步骤。",
    "System management context is visible.": "检测到当前窗口包含系统管理信息。",
    "Resume task": "继续任务",
    "Resume the most recent unfinished task using the current screen context.": "结合当前屏幕上下文继续最近未完成的任务。",
    "Session history has unfinished tasks.": "检测到会话中存在未完成的任务。"
  };
  if (exact[text]) return exact[text];
  for (const [prefix, translatedPrefix] of Object.entries(exact)) {
    if (prefix.endsWith(":") && text.startsWith(prefix)) {
      return `${translatedPrefix}${text.slice(prefix.length).trimStart()}`;
    }
  }
  if (text.startsWith("SafetyReviewAgent stopped the task after ") && text.endsWith(" consultation.")) {
    const agent = text
      .replace("SafetyReviewAgent stopped the task after ", "")
      .replace(" consultation.", "");
    return `安全审核 Agent 在 ${zhAgentName(agent)} 咨询后拦截了任务。`;
  }
  if (text.startsWith("Denied step: ")) {
    return `步骤已被拒绝：${text.replace("Denied step: ", "")}`;
  }
  if (text.startsWith("任务执行失败：")) {
    return `任务执行失败：${zhBackendText(text.replace("任务执行失败：", "").trim())}`;
  }
  if (text.includes("Explicit absolute path is required when no authorized directories are configured.")) {
    return text.replace(
      "Explicit absolute path is required when no authorized directories are configured.",
      "请先选择要整理的文件夹，或填写完整的文件位置，例如桌面、下载、文档、图片里的某个文件。"
    );
  }
  if (text.includes("No authorized directories configured.")) {
    return text.replace("No authorized directories configured.", "还没有选择要整理的文件夹。请先添加桌面、下载、文档或图片。");
  }
  if (text.includes("Path is outside authorized directories.")) {
    return text.replace("Path is outside authorized directories.", "这个文件不在你已选择的文件夹里。请换一个文件，或先把所在文件夹加入设置。");
  }
  if (text.startsWith("Calling tool ")) {
    return `正在调用工具：${text.replace("Calling tool ", "").replace(".", "")}`;
  }
  if (text.endsWith(" dry-run preview generated.")) {
    return `已生成 ${text.replace(" dry-run preview generated.", "")} 的试运行预览。`;
  }
  if (text.endsWith(" completed.")) {
    return `${text.replace(" completed.", "")} 已完成。`;
  }
  if (text.endsWith(" failed.")) {
    return `${text.replace(" failed.", "")} 执行失败。`;
  }
  const generatedPlanMatch = text.match(/^Generated plan with (\d+) step\(s\)\.$/);
  if (generatedPlanMatch) {
    return `已生成包含 ${generatedPlanMatch[1]} 个步骤的计划。`;
  }
  const supervisionMatch = text.match(/^(.+): (.+) message supervision -> (.+)$/);
  if (supervisionMatch) {
    return `${supervisionMatch[1]}：${zhAgentName(supervisionMatch[2])} 消息监督 -> ${zhSafetyVerdict(supervisionMatch[3])}`;
  }
  const postToolMatch = text.match(/^(.+): post-tool supervision -> (.+)$/);
  if (postToolMatch) {
    return `${zhToolName(postToolMatch[1])}：工具结果监督 -> ${zhSafetyVerdict(postToolMatch[2])}`;
  }
  const toolReviewMatch = text.match(/^(.+): (.+) \((.+)\)$/);
  if (toolReviewMatch) {
    return `${zhToolName(toolReviewMatch[1])}：${zhSafetyVerdict(toolReviewMatch[2])}（${zhRiskLevel(toolReviewMatch[3])}）`;
  }
  if (text === "(matching authorized files)") return "匹配到的授权文件";
  if (text === "(choose target folder after approval)") return "审批后选择目标文件夹";
  return text;
}

export function zhUserFacingError(text?: string): string {
  const raw = String(text ?? "").trim();
  if (!raw) return "";
  const compact = raw.replace(/\s+/g, " ");
  const lower = compact.toLowerCase();

  if (lower.includes("mockprovider")) {
    return "当前 AI 服务临时不可用，我已切到临时模式，结果可能不完整。请稍后重试，或在设置里检查 AI 服务。";
  }

  if (
    lower.includes("no authorized directories configured") ||
    compact.includes("未配置授权目录") ||
    compact.includes("尚未配置授权目录")
  ) {
    return "还没有选择要整理的文件夹。请先在设置里添加桌面、下载、文档或图片；在你确认前，我只会查看文件。";
  }

  if (
    lower.includes("explicit absolute path is required") ||
    compact.includes("未配置授权目录时，必须提供明确的绝对路径") ||
    compact.includes("必须提供明确的绝对路径")
  ) {
    return "请先选择要整理的文件夹，或填写完整的文件位置，例如桌面、下载、文档、图片里的某个文件。";
  }

  if (
    lower.includes("path is outside authorized directories") ||
    lower.includes("outside authorized directories") ||
    compact.includes("不在授权目录")
  ) {
    return "这个文件不在你已选择的文件夹里。请换一个文件，或先把所在文件夹加入设置。";
  }

  if (
    lower.includes("file paths must stay inside authorized directories") ||
    compact.includes("文件路径必须保持在授权目录内")
  ) {
    return "为了保护你的文件，只会处理你先选择过的文件夹；移动、重命名或删除前会先给你确认。";
  }

  if (
    compact.includes("没有可分组的索引文件") ||
    compact.includes("暂无索引") ||
    compact.includes("未建立索引") ||
    lower.includes("no indexed file") ||
    lower.includes("no indexed files") ||
    lower.includes("no index result") ||
    lower.includes("no index results")
  ) {
    return "还没找到可搜索的文件。请先在设置里选择要整理的文件夹，或换成文件名试试。";
  }

  if (
    lower.includes("missing desktop api token") ||
    lower.includes("http_401") ||
    lower.includes("http 401") ||
    lower.includes("status 401") ||
    lower.includes("unauthorized")
  ) {
    return "Lengrvis 正在保护本机接口。请通过桌面应用连接，或重启 Lengrvis 后再试；未授权的浏览器页面不能直接读取本机数据。";
  }

  if (
    lower.includes("1008") ||
    lower.includes("policy violation") ||
    lower.includes("policy_violation")
  ) {
    return "实时连接被后端安全策略关闭（1008）。请确认桌面端和后端授权一致，必要时重启 Lengrvis 后再试。";
  }

  if (
    lower.includes("http_404") ||
    lower.includes("http 404") ||
    lower.includes("status 404") ||
    lower.includes("404") ||
    lower.includes("not found")
  ) {
    return "这个功能入口暂时不可用。请确认 Lengrvis 正在运行并已更新，然后重试。";
  }

  if (
    lower.includes("backend request failed") ||
    lower.includes("failed to fetch") ||
    lower.includes("networkerror") ||
    lower.includes("network error") ||
    lower.includes("load failed") ||
    lower.includes("econnrefused") ||
    lower.includes("connection refused") ||
    compact.includes("等待后端连接") ||
    compact.includes("后端连接失败")
  ) {
    return "Lengrvis 暂时没连接上。请先启动或重启 Lengrvis，再重试。";
  }

  if (
    lower.includes("renderer api requests must use backend-relative endpoints") ||
    lower.includes("backend-relative")
  ) {
    return "这个功能没有正确连接到 Lengrvis。请重启应用后再试。";
  }

  if (
    lower.includes("aborterror") ||
    lower.includes("aborted") ||
    lower.includes("timeout") ||
    compact.includes("超时")
  ) {
    return "这一步等得有点久。请稍后重试，或先缩小要整理的文件夹范围。";
  }

  if (containsUserVisibleSensitiveDetail(compact)) {
    return "这一步没有完成。为保护本机信息，详细错误已隐藏；请在电脑端日志或诊断包里核对。";
  }

  return raw;
}

function containsUserVisibleSensitiveDetail(value: string): boolean {
  return (
    /(^\s*[{[]|["']?(?:args|tool_args|arguments|headers|authorization|protocol|host|hostname|base_url|url|path)["']?\s*:)/i.test(value) ||
    /\bBearer\s+[A-Za-z0-9._~+/=-]+/i.test(value) ||
    /\b(?:token|access_token|auth|authorization|api[_-]?key|secret|password|session[_-]?token)\b\s*[:=]\s*["']?[^"',;\s})\]]+/i.test(value) ||
    /\b(?:https?|wss?|file):\/\/[^\s,;)\]}>]+/i.test(value) ||
    /\b(?:localhost|(?:\d{1,3}\.){3}\d{1,3})(?::\d{2,5})?\b/i.test(value) ||
    /\b[A-Za-z]:[\\/][^\s,;)\]}>]+/i.test(value) ||
    /\\\\[^\\/\s]+\\[^\s,;)\]}>]+/i.test(value) ||
    /(^|[\s(["'])\/(?:Users|home|var|tmp|etc|mnt|Volumes|private|opt|usr|workspace|root|srv|Desktop|Downloads)\b/i.test(value)
  );
}

function naturalSupervisorReply(text: string): string {
  const normalized = text
    .replace(/\s+/g, "")
    .replace(/[，。；：,.!！?？]/g, "");
  const mojibakeTemplate = "ä¸»ç®¡Agentå·²æ¶å°";
  const isTemplate =
    normalized.includes("主管Agent已收到") ||
    normalized.includes("确认意图") ||
    text.includes(mojibakeTemplate);
  if (!isTemplate) return "";
  return "我在，咱们正常聊。你可以直接问我问题或说想法；需要我实际操作电脑、文件、网页或应用时，我再安排对应 Agent。";
}

export function zhToolName(name?: string) {
  if (!name) return "未知工具";
  const labels: Record<string, string> = {
    "file.search_by_name": "按名称搜索文件",
    "file.search_full_text": "全文搜索文件",
    "file.semantic_search": "语义搜索文件",
    "file.list_directory": "列出目录",
    "file.get_metadata": "读取文件元数据",
    "file.hash_file": "计算文件哈希",
    "file.find_duplicates": "查找重复文件",
    "file.preview_batch_operation": "预览批量文件操作",
    "file.create_folder": "创建文件夹",
    "file.copy": "复制文件",
    "file.move": "移动文件",
    "file.rename": "重命名文件",
    "file.trash": "移入回收站",
    "file.write_text": "写入文本文件",
    "file.generate_markdown_report": "生成 Markdown 报告",
    "document.parse": "解析文档",
    "document.extract_text": "提取文档文本",
    "document.summarize": "总结文档",
    "document.qa": "文档问答",
    "document.compare": "对比文档",
    "document.analyze_csv": "分析 CSV",
    "document.analyze_xlsx": "分析表格",
    "files.cleanup.scan": "扫描清理项",
    "files.cleanup.plan": "生成清理计划",
    "files.cleanup.execute": "执行清理计划",
    "files.cleanup.rollback": "回滚清理计划",
    "system.info": "读取系统信息",
    "system.diagnostics": "系统诊断",
    "system.processes": "读取进程摘要",
    "system.startup_items": "读取启动项",
    "system.open_settings_uri": "打开系统设置",
    "app.list_installed": "列出已安装应用",
    "app.launch_installed": "启动已安装应用",
    "app.open_file": "打开文件",
    "app.open_folder": "打开文件夹",
    "app.reveal_in_explorer": "在资源管理器中显示",
    "browser.read_page": "读取网页",
    "browser.screenshot": "网页截图",
    "browser.extract_links": "提取网页链接",
    "search.query": "搜索查询"
  };
  return labels[name] ?? name;
}

export function zhSeverity(severity: SafetySeverity) {
  const labels: Record<SafetySeverity, string> = {
    low: "低",
    medium: "中",
    high: "高",
    critical: "严重"
  };
  return labels[severity] ?? severity;
}

export function zhFindingStatus(status: SafetyFinding["status"]) {
  const labels: Record<SafetyFinding["status"], string> = {
    open: "待处理",
    accepted: "已接受",
    dismissed: "已忽略"
  };
  return labels[status] ?? status;
}

export function zhApprovalStatus(status: ApprovalRequest["status"]) {
  const labels: Record<ApprovalRequest["status"], string> = {
    pending: "待审批",
    approved: "已批准",
    denied: "已拒绝",
    expired: "已过期",
    unavailable: "不可操作"
  };
  return labels[status] ?? status;
}

export function zhRole(role: ChatRole) {
  const labels: Record<ChatRole, string> = {
    system: "系统",
    developer: "开发者",
    user: "用户",
    assistant: "助手",
    tool: "工具"
  };
  return labels[role] ?? role;
}

export function zhMessageKind(kind?: NonNullable<AgentMessage["kind"]>) {
  if (!kind) return "";
  const labels: Record<NonNullable<AgentMessage["kind"]>, string> = {
    handoff: "交接",
    observation: "观察",
    action: "动作",
    result: "结果"
  };
  return labels[kind] ?? kind;
}

export function zhAuditLevel(level: AuditLogEntry["level"]) {
  const labels: Record<AuditLogEntry["level"], string> = {
    info: "信息",
    warning: "警告",
    error: "错误"
  };
  return labels[level] ?? level;
}

export function zhAgentName(value?: string) {
  if (!value) return "未知";
  const normalized = value.toLowerCase();
  if (normalized === "you" || normalized === "user") return "你";
  if (normalized === "assistant") return "助手";
  if (normalized.includes("orchestrator")) return "调度 Agent";
  if (normalized.includes("planner")) return "规划 Agent";
  if (normalized.includes("file")) return "文件 Agent";
  if (normalized.includes("document")) return "文档 Agent";
  if (normalized.includes("computer") || normalized.includes("system")) return "电脑 Agent";
  if (normalized.includes("app")) return "应用 Agent";
  if (normalized.includes("browser")) return "浏览器 Agent";
  if (normalized.includes("search")) return "搜索 Agent";
  if (normalized.includes("safety")) return "安全审核 Agent";
  if (normalized.includes("human")) return "人工审批";
  if (normalized.includes("desktop")) return "桌面端";
  if (normalized.includes("index")) return "索引器";
  return value;
}

export function zhAuditAction(action: string) {
  const labels: Record<string, string> = {
    opened: "已打开",
    flagged: "已标记",
    "health-check": "健康检查",
    "open-settings-failed": "打开设置失败",
    "task.created": "创建任务",
    "task.finished_or_waiting": "任务完成或等待",
    "browser.read_page": "读取网页",
    "browser.open_url": "打开网页",
    "browser.screenshot": "网页截图",
    "app.launch_allowlisted": "启动授权应用",
    "app.launch_installed": "启动已安装应用",
    "app.open_file": "打开文件",
    "app.open_folder": "打开文件夹",
    "app.reveal_in_explorer": "在资源管理器中显示",
    "system.open_settings_uri": "打开系统设置",
    "safety.goal_review": "目标安全审核",
    "safety.plan_review": "计划安全审核",
    "safety.tool_call_review": "工具调用审核",
    "safety.tool_result_review": "工具结果审核",
    "safety.agent_message_review": "Agent 消息审核"
  };
  return labels[action] ?? action;
}

export function zhSource(source?: string) {
  const labels: Record<string, string> = {
    builtin: "内置",
    start_menu: "开始菜单",
    registry: "注册表",
    startup_folder: "启动文件夹",
    HKCU: "当前用户",
    HKLM: "本机"
  };
  return labels[source ?? ""] ?? source ?? "未知";
}

export function zhSystemSuggestion(text: string) {
  const exact: Record<string, string> = {
    "No critical system issue detected from read-only diagnostics.": "只读诊断未发现关键系统问题。",
    "Memory is low; close large apps before running heavy automation.": "可用内存偏低，运行重型自动化前建议关闭大型应用。"
  };
  return exact[text] ?? text;
}

export function zhRelativeTime(value: string) {
  const minutes = Math.max(1, Math.round((Date.now() - new Date(value).getTime()) / 60_000));
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.round(hours / 24)} 天前`;
}
export function zhRealtimeConnectionStatus(status: {
  state: string;
  code?: number;
  reason?: string;
  retryInMs?: number;
  message?: string;
}): string {
  const retry = status.retryInMs ? `，约 ${Math.round(status.retryInMs / 1000)} 秒后重试` : "";
  const detail = status.code || status.reason ? `（${[status.code ? `code ${status.code}` : "", status.reason].filter(Boolean).join(" / ")}）` : "";
  if (status.state === "open") {
    return "实时连接已建立，任务进度会自动更新。";
  }
  if (status.state === "connecting") {
    return "正在连接实时通道。";
  }
  if (status.state === "unauthorized") {
    return `实时连接未通过桌面授权${detail}。请重启 Lengrvis 桌面端后再试。`;
  }
  if (status.state === "policy_violation") {
    return `实时连接被后端安全策略拒绝${detail}。请确认桌面端和后端使用同一个 desktop token。`;
  }
  if (status.state === "reconnecting") {
    return `实时连接暂时断开，正在恢复${retry}${detail}。期间会继续用轮询刷新任务状态。`;
  }
  if (status.state === "error") {
    return `${status.message || "实时连接遇到错误，正在尝试恢复"}${retry}${detail}。`;
  }
  if (status.state === "bad_message") {
    return "收到一条无法解析的实时消息，已隐藏原始内容并继续等待下一条。";
  }
  if (status.state === "closed") {
    return `实时连接已断开${detail}。可以刷新连接，任务状态仍会通过轮询补齐。`;
  }
  return status.message || "实时连接状态已更新。";
}

export function zhRealtimeShortStatus(status: { state: string }): string {
  if (status.state === "unauthorized") return "未授权";
  if (status.state === "policy_violation") return "1008 拒绝";
  if (status.state === "reconnecting") return "重连中";
  if (status.state === "connecting") return "连接中";
  if (status.state === "bad_message") return "消息异常";
  if (status.state === "closed") return "已断线";
  if (status.state === "error") return "实时异常";
  return "就绪";
}

export function zhRealtimeBadMessageSummary(count: number, samples: string[]): string {
  const sampleText = samples.length
    ? ` 最近安全摘要：${samples.map((sample) => `“${previewRealtimeSample(sample)}”`).join("；")}`
    : "";
  return `实时链路收到 ${count} 条无法解析的消息，已隐藏原始内容并继续监听。${sampleText}`;
}

function previewRealtimeSample(sample: string): string {
  const compact = redactRealtimePreview(sample.replace(/\s+/g, " ").trim());
  return compact.length > 220 ? `${compact.slice(0, 220)}...` : compact;
}

function redactRealtimePreview(value: string): string {
  if (!value) return "";
  if (
    /(^\s*[{[]|["']?(?:args|tool_args|arguments|headers|authorization|protocol|host|hostname|base_url|url|path)["']?\s*:)/i.test(value) ||
    /\bBearer\s+[A-Za-z0-9._~+/=-]+/i.test(value) ||
    /\b(?:token|access_token|auth|authorization|api[_-]?key|secret|password|session[_-]?token)\b\s*[:=]\s*["']?[^"',;\s})\]]+/i.test(value) ||
    /\b(?:https?|wss?|file):\/\/[^\s,;)\]}>]+/i.test(value) ||
    /\b(?:localhost|(?:\d{1,3}\.){3}\d{1,3})(?::\d{2,5})?\b/i.test(value) ||
    /\b[A-Za-z]:[\\/][^\s,;)\]}>]+/i.test(value) ||
    /\\\\[^\\/\s]+\\[^\s,;)\]}>]+/i.test(value) ||
    /(^|[\s(["'])\/(?:Users|home|var|tmp|etc|mnt|Volumes|private|opt|usr|workspace|root|srv|Desktop|Downloads)\b/i.test(value)
  ) {
    return "原始内容已隐藏，避免显示本机路径、连接地址或凭据。";
  }
  return value;
}
