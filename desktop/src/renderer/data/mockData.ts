import type {
  AgentConversation,
  AppSettings,
  ApprovalRequest,
  AuditLogEntry,
  ChatMessage,
  FileSearchResult,
  Plan,
  SafetyReview,
  SystemInfo,
  TaskEvent
} from "../../shared/types";

const now = new Date();
const iso = (minutesAgo: number) => new Date(now.getTime() - minutesAgo * 60_000).toISOString();

export const sampleChatMessages: ChatMessage[] = [
  {
    id: "chat-1",
    role: "system",
    author: "Mavris",
    content: "工作区已初始化。后端状态会显示在系统面板里。",
    createdAt: iso(48),
    status: "sent"
  },
  {
    id: "chat-2",
    role: "assistant",
    author: "调度 Agent",
    content: "当前计划已加载，其中有一个动作正在等待安全审核。",
    createdAt: iso(17),
    status: "sent"
  }
];

export const sampleTaskTimeline: TaskEvent[] = [
  {
    id: "task-1",
    title: "收集工作区上下文",
    description: "已索引项目文件和活跃工作区域。",
    state: "completed",
    agent: "索引器",
    createdAt: iso(74),
    updatedAt: iso(66)
  },
  {
    id: "task-2",
    title: "生成执行计划",
    description: "已准备前端、IPC 和安全面的分阶段工作。",
    state: "running",
    agent: "规划 Agent",
    createdAt: iso(31),
    updatedAt: iso(12)
  },
  {
    id: "task-3",
    title: "等待审批",
    description: "文件写入操作需要确认后才能执行。",
    state: "blocked",
    agent: "安全审核 Agent",
    createdAt: iso(10),
    updatedAt: iso(8)
  }
];

export const samplePlan: Plan = {
  id: "plan-current",
  title: "电脑 AI 管家任务",
  objective: "用多 Agent 协作处理文件、文档、电脑状态和安全审批。",
  updatedAt: iso(5),
  steps: [
    {
      id: "step-1",
      title: "理解用户目标",
      detail: "把自然语言任务拆成可审核的步骤。",
      state: "done",
      owner: "桌面端"
    },
    {
      id: "step-2",
      title: "调用专业 Agent",
      detail: "让文件、电脑、浏览器等 Agent 分别评估自己的部分。",
      state: "active",
      owner: "前端"
    },
    {
      id: "step-3",
      title: "安全审核",
      detail: "涉及修改、删除和系统设置时先生成预览并等待确认。",
      state: "pending",
      owner: "安全审核 Agent"
    }
  ]
};

export const sampleAgentConversations: AgentConversation[] = [
  {
    id: "agent-convo-1",
    title: "实现循环",
    status: "running",
    messages: [
      {
        id: "agent-msg-1",
        role: "assistant",
        name: "规划 Agent",
        agent: "规划 Agent",
        kind: "observation",
        content: "渲染进程和主进程契约已准备好交接。",
        createdAt: iso(19)
      },
      {
        id: "agent-msg-2",
        role: "assistant",
        name: "桌面端 Agent",
        agent: "桌面端 Agent",
        kind: "action",
        content: "正在应用 React 工作台外壳和 IPC 桥接。",
        createdAt: iso(6)
      }
    ]
  },
  {
    id: "agent-convo-2",
    title: "安全通道",
    status: "waiting",
    messages: [
      {
        id: "agent-msg-3",
        role: "assistant",
        name: "安全审核 Agent",
        agent: "安全审核 Agent",
        kind: "handoff",
        content: "审批队列里有一个待处理的高风险请求。",
        createdAt: iso(8)
      }
    ]
  }
];

export const sampleSafetyReview: SafetyReview = {
  id: "safety-current",
  status: "needs_review",
  updatedAt: iso(7),
  findings: [
    {
      id: "finding-1",
      severity: "high",
      title: "外部命令待确认",
      detail: "在明确配置命令前，后端自动启动保持关闭。",
      status: "open"
    },
    {
      id: "finding-2",
      severity: "medium",
      title: "网络依赖",
      detail: "API 操作依赖配置的后端地址可访问。",
      status: "accepted"
    }
  ]
};

export const sampleApprovalRequests: ApprovalRequest[] = [
  {
    id: "approval-1",
    title: "运行已配置的后端进程",
    reason: "桌面端可以启动本地后端命令。",
    requester: "桌面端 Agent",
    riskLevel: "high",
    createdAt: iso(9),
    proposedAction: "使用 MAVRIS_BACKEND_ARGS 启动 MAVRIS_BACKEND_COMMAND。",
    status: "pending"
  }
];

export const sampleFileResults: FileSearchResult[] = [
  {
    id: "file-1",
    path: "desktop/src/renderer/App.tsx",
    match: "工作台网格布局",
    line: 42,
    score: 0.92
  },
  {
    id: "file-2",
    path: "desktop/src/main/ipc.ts",
    match: "相对后端的 API 请求代理",
    line: 16,
    score: 0.87
  }
];

export const sampleSettings: AppSettings = {
  apiBaseUrl: "http://127.0.0.1:8000",
  autoStartBackend: false,
  telemetryEnabled: false,
  compactMode: false,
  theme: "system",
  workspaceRoot: "C:\\Users\\Suli\\Desktop\\mavris",
  allowBrowserNetwork: false,
  remoteDesktopEnabled: false,
  appAllowlist: ["notepad", "calculator", "calc"],
  browserMaxPageBytes: 250000,
  browserScreenshotDir: "C:\\Users\\Suli\\Desktop\\mavris\\.marvis_data\\browser_screenshots",
  onnxModelPath: "",
  onnxExecutionProvider: "",
  mode: "privacy",
  allowCloudContext: false,
  allowFileContentUpload: false,
  mcpServers: []
};

export const sampleAuditLogs: AuditLogEntry[] = [
  {
    id: "audit-1",
    actor: "桌面端",
    action: "opened",
    target: "工作区",
    level: "info",
    createdAt: iso(51)
  },
  {
    id: "audit-2",
    actor: "安全审核 Agent",
    action: "flagged",
    target: "后端启动",
    level: "warning",
    createdAt: iso(9)
  },
  {
    id: "audit-3",
    actor: "API",
    action: "health-check",
    target: "http://127.0.0.1:8000",
    level: "error",
    createdAt: iso(2)
  }
];

export const sampleSystemInfo: SystemInfo = {
  appVersion: window.mavris?.versions.app ?? "0.1.0",
  electronVersion: window.mavris?.versions.electron ?? "未知",
  chromeVersion: window.mavris?.versions.chrome ?? "未知",
  nodeVersion: window.mavris?.versions.node ?? "未知",
  platform: window.mavris?.platform ?? "win32",
  arch: "x64",
  backendBaseUrl: sampleSettings.apiBaseUrl,
  diagnostics: {
    info: {
      cpu_count: 12,
      memory_total: 34359738368,
      memory_available: 17179869184
    },
    disks: [
      {
        device: "C:",
        mountpoint: "C:\\",
        fstype: "NTFS",
        usage: {
          total: 1024 * 1024 * 1024 * 512,
          used: 1024 * 1024 * 1024 * 241,
          free: 1024 * 1024 * 1024 * 271,
          percent: 47
        }
      }
    ],
    network: {},
    battery: null,
    topProcesses: [
      {
        pid: 8844,
        name: "Code.exe",
        cpuPercent: 2,
        memoryBytes: 1024 * 1024 * 620,
        status: "running"
      },
      {
        pid: 4420,
        name: "chrome.exe",
        cpuPercent: 1,
        memoryBytes: 1024 * 1024 * 410,
        status: "running"
      }
    ],
    startupItems: [
      {
        name: "Mavris 助手",
        source: "startup_folder"
      }
    ],
    suggestions: ["只读诊断未发现关键系统问题。"]
  },
  processes: [
    {
      pid: 8844,
      name: "Code.exe",
      cpuPercent: 2,
      memoryBytes: 1024 * 1024 * 620,
      status: "running"
    }
  ],
  startupItems: [
    {
      name: "Mavris 助手",
      source: "startup_folder"
    }
  ],
  installedApps: [
    {
      id: "notepad",
      name: "notepad",
      command: "notepad.exe",
      path: "notepad.exe",
      source: "builtin",
      allowlisted: true
    },
    {
      id: "calc",
      name: "calc",
      command: "calc.exe",
      path: "calc.exe",
      source: "builtin",
      allowlisted: true
    }
  ]
};
