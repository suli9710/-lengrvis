export interface TaskStarterManifest {
  id: "clean-downloads" | "summarize-document" | "find-large-files" | "check-computer" | "document-qa";
  title: string;
  summary: string;
  inputHint: string;
  preflight: string[];
  trust: {
    local: string;
    cloud: string;
    approval: string;
    rollback: string;
    estimate: string;
  };
  outputType: string;
}

export const taskStarterManifests: TaskStarterManifest[] = [
  {
    id: "clean-downloads",
    title: "整理下载目录",
    summary: "先盘点、分组、生成清理计划，删除前必须审批。",
    inputHint: "选择或确认下载目录，也可以直接使用默认 Downloads。",
    preflight: ["目录可访问", "文件索引可用", "删除动作会先生成预览"],
    trust: {
      local: "本机文件名、大小、类型和路径在本机处理",
      cloud: "默认不上传文件正文；混合模式只允许上传脱敏计划",
      approval: "移动或删除前审批",
      rollback: "回收站/移动类动作可回滚",
      estimate: "3-6 分钟"
    },
    outputType: "清理计划"
  },
  {
    id: "summarize-document",
    title: "总结本地文档",
    summary: "选择文档后生成摘要、要点和引用来源。",
    inputHint: "选择 PDF、DOCX、TXT、PPTX、XLSX 或 CSV。",
    preflight: ["文档在授权目录内", "文本/OCR 可抽取", "引用来源可展示"],
    trust: {
      local: "文档抽取、OCR 和引用定位优先留在本机",
      cloud: "快速/混合模式可能上传必要片段；隐私模式不上云",
      approval: "只读",
      rollback: "不修改文件",
      estimate: "1-3 分钟"
    },
    outputType: "带引用摘要"
  },
  {
    id: "find-large-files",
    title: "查找大文件",
    summary: "找出占空间文件并输出可导出的清单。",
    inputHint: "选择扫描范围，或使用已授权目录。",
    preflight: ["扫描范围已授权", "只读取文件元数据", "清理前会二次确认"],
    trust: {
      local: "文件路径、大小和修改时间在本机处理",
      cloud: "默认不上传路径；混合模式只上传脱敏统计",
      approval: "清理建议需要审批",
      rollback: "移动类动作可回滚",
      estimate: "2-5 分钟"
    },
    outputType: "大文件表格"
  },
  {
    id: "check-computer",
    title: "检查电脑状态",
    summary: "只读检查后端、系统、磁盘、进程和本地 AI 状态。",
    inputHint: "无需输入，点击即可开始只读检查。",
    preflight: ["后端健康检查", "系统信息只读", "不会改动设置"],
    trust: {
      local: "系统状态、磁盘和进程信息只在本机读取",
      cloud: "不上云",
      approval: "只读",
      rollback: "不修改系统",
      estimate: "30 秒"
    },
    outputType: "电脑体检卡"
  },
  {
    id: "document-qa",
    title: "文档问答",
    summary: "选择本地文档后带来源回答问题。",
    inputHint: "选择文档并输入一个具体问题。",
    preflight: ["文档在授权目录内", "问题已填写", "答案必须带来源"],
    trust: {
      local: "文档抽取、chunk、embedding 和引用优先留本机",
      cloud: "快速/混合模式可能上传必要片段；隐私模式不上云",
      approval: "只读",
      rollback: "不修改文件",
      estimate: "1-4 分钟"
    },
    outputType: "带引用问答"
  }
];

export function taskStarterManifestById(id: string): TaskStarterManifest | undefined {
  return taskStarterManifests.find((item) => item.id === id);
}
