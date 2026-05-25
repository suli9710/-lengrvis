import type {
  AgentMessage,
  AgentConversation,
  ApprovalRequest,
  AuditLogEntry,
  BackendState,
  ChatRole,
  SafetyFinding,
  SafetyReview,
  SafetySeverity,
  TaskState,
  PlanStepState
} from "../../shared/types";

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
    completed: "已完成",
    failed: "失败"
  };
  return labels[state] ?? state;
}

export function zhBackendTaskStatus(status?: string) {
  if (!status) return "未知";
  const labels: Record<string, string> = {
    queued: "排队中",
    planning: "规划中",
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
  return labels[status] ?? status;
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
    system_change: "系统变更审批",
    browser_action: "浏览器操作审批",
    app_launch: "应用启动审批"
  };
  return labels[type] ?? type;
}

export function zhBackendText(text?: string) {
  if (!text) return "";
  const exact: Record<string, string> = {
    "Primary provider failed; using MockProvider fallback:": "主模型调用失败，已切换到 MockProvider 兜底：",
    "Provider returned invalid plan; using MockProvider fallback:": "模型返回的计划格式无效，已切换到 MockProvider 兜底：",
    "File paths must stay inside authorized directories; modifying steps need dry-run previews.": "文件路径必须保持在授权目录内；修改类步骤需要先生成试运行预览。",
    "System inspection is read-only unless a Windows settings operation is explicitly approved.": "系统检查默认只读；只有明确审批后才允许执行 Windows 设置类操作。",
    "Application operations are limited to indexed apps and authorized file/folder open actions; unknown executables require approval or are blocked.": "应用操作仅限已索引应用和授权文件/文件夹打开动作；未知可执行文件需要审批或会被拦截。",
    "Browser operations start read-only; login, payment, submission, and messaging are handoff-only.": "浏览器操作默认只读；登录、支付、提交表单和发消息都必须交给人工处理。",
    "External search results must preserve source URL, title, summary, and retrieval time.": "外部搜索结果必须保留来源 URL、标题、摘要和检索时间。",
    "SafetyReviewAgent stopped the task during initial runtime supervision.": "安全审核 Agent 在初始运行监督中拦截了任务。",
    "SafetyReviewAgent stopped the task after PlannerAgent output.": "安全审核 Agent 在规划输出后拦截了任务。",
    "SafetyReviewAgent stopped the task before executing a tool call.": "安全审核 Agent 在执行工具调用前拦截了任务。",
    "SafetyReviewAgent stopped the task after observing tool output.": "安全审核 Agent 在观察工具结果后拦截了任务。",
    "Plan generated and waiting for approval on modifying steps.": "计划已生成，修改类步骤正在等待审批。",
    "Task completed with read-only/open-only MVP tools.": "任务已通过只读或打开类工具完成。",
    "Waiting for user approval before executing modifying operation.": "正在等待用户审批，审批后才会执行修改操作。",
    "Preview only. Approval is required before any file is moved, copied, renamed, or deleted.": "当前仅为预览。移动、复制、重命名或删除文件前必须先获得审批。"
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
    "document.summarize": "总结文档",
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
    denied: "已拒绝"
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
