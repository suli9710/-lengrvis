import type { MobileTaskMode, MobileTaskTemplateId } from "./api/client";

export interface TaskStarterTemplate {
  id: MobileTaskTemplateId;
  title: string;
  summary: string;
  inputHint: string;
  outputType: string;
  mode: MobileTaskMode;
}

export const taskStarterTemplates: TaskStarterTemplate[] = [
  {
    id: "organize_downloads",
    title: "整理下载目录",
    summary: "生成清理计划，删除前必须审批。",
    inputHint: "可补充目录或整理规则",
    outputType: "清理计划",
    mode: "hybrid"
  },
  {
    id: "summarize_local_docs",
    title: "总结本地文档",
    summary: "让电脑端选择文档并生成引用摘要。",
    inputHint: "可补充要重点总结什么",
    outputType: "带引用摘要",
    mode: "hybrid"
  },
  {
    id: "find_large_files",
    title: "查找大文件",
    summary: "列出占空间文件和清理建议。",
    inputHint: "可补充扫描范围",
    outputType: "大文件表格",
    mode: "hybrid"
  },
  {
    id: "check_computer_status",
    title: "检查电脑状态",
    summary: "只读检查系统和本地 AI 状态。",
    inputHint: "无需补充",
    outputType: "电脑体检卡",
    mode: "efficiency"
  },
  {
    id: "document_qa",
    title: "文档问答",
    summary: "让电脑端选择文档并带来源回答。",
    inputHint: "输入你想问的问题",
    outputType: "带引用问答",
    mode: "hybrid"
  }
];
