import {
  BookOpenText,
  FileSearch,
  FolderOpen,
  Laptop,
} from "lucide-react";

import type { OfficeQuickSkill } from "./OfficeScene";

export const quickSkills: OfficeQuickSkill[] = [
  {
    id: "check-computer",
    icon: Laptop,
    title: "检查电脑状态",
    summary: "无需输入，先做只读快照",
    kind: "action",
    action: "system-check",
    trust: { local: "本机读取", cloud: "不上云", approval: "只读", rollback: "无改动", estimate: "30 秒" },
    wizard: {
      input: "一句话可选：帮我检查这台电脑",
      preflight: "只读取后端、系统和本地模型状态",
      output: "健康状态、缺失依赖、下一步修复入口",
      nextStep: "打开电脑状态页并刷新只读快照"
    }
  },
  {
    id: "clean-downloads",
    icon: FolderOpen,
    title: "整理下载目录",
    summary: "一句话扫描，先出清理计划",
    kind: "prompt",
    prompt: "扫描我的下载目录，按安装包、文档、图片、压缩包和临时文件分组，给出整理建议；不要直接删除任何文件。",
    trust: { local: "本机文件", cloud: "默认不传正文", approval: "删除需审批", rollback: "回收站可恢复", estimate: "3-6 分钟" },
    wizard: {
      input: "一句话或下载目录",
      preflight: "确认授权目录，只读盘点风险",
      output: "分组清单、空间估算、审批预览",
      nextStep: "点发送后只生成清理计划，删除前会停下审批"
    }
  },
  {
    id: "summarize-document",
    icon: BookOpenText,
    title: "总结本地文档",
    summary: "先选文件，摘要带引用",
    kind: "view",
    view: "files",
    trust: { local: "本机读取", cloud: "按模式", approval: "只读", rollback: "无改动", estimate: "1-3 分钟" },
    wizard: {
      input: "PDF/DOCX/TXT 等本地文档",
      preflight: "同步文件范围并读取预览",
      output: "摘要、重点、引用来源",
      nextStep: "进入文档操作区，选择文档后总结"
    }
  },
  {
    id: "find-large-files",
    icon: FileSearch,
    title: "查找大文件",
    summary: "先列排行，不移动文件",
    kind: "prompt",
    prompt: "找出这台电脑上最大的文件，并按安全清理、需要确认、建议保留三类给出建议；不要直接删除任何文件。",
    trust: { local: "本机索引", cloud: "默认不传路径", approval: "清理需审批", rollback: "先审再处理", estimate: "2-5 分钟" },
    wizard: {
      input: "文件范围和大小阈值",
      preflight: "只扫描授权目录，不触碰未授权路径",
      output: "大文件排行、保留建议、清理候选",
      nextStep: "点发送后先选文件夹，再生成只读结果"
    }
  },
  {
    id: "document-qa",
    icon: BookOpenText,
    title: "文档问答",
    summary: "选择文件后带来源回答",
    kind: "view",
    view: "files",
    trust: { local: "本机抽取", cloud: "按模式", approval: "只读", rollback: "无改动", estimate: "1-4 分钟" },
    wizard: {
      input: "一份文档和一个问题",
      preflight: "读取选中文档并保留引用块",
      output: "带来源回答、可继续追问",
      nextStep: "进入文档操作区，选择文档后提问"
    }
  }
];
