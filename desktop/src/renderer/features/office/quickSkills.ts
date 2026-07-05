import type { OfficeQuickSkill } from "./OfficeScene";

export const quickSkills: OfficeQuickSkill[] = [
  {
    id: "check-computer",
    title: "检查电脑状态",
    summary: "只读快照",
    kind: "prompt",
    prompt: "帮我检查这台电脑",
    trust: { local: "本机", cloud: "不上云", approval: "只读", rollback: "无改动", estimate: "30 秒" },
    wizard: {
      input: "帮我检查这台电脑",
      preflight: "读状态，不改设置",
      output: "健康状态、缺失依赖",
      nextStep: "点击发送后启动只读电脑检查"
    }
  },
  {
    id: "clean-downloads",
    title: "整理下载目录",
    summary: "清理前先预览",
    kind: "prompt",
    prompt: "扫描我的下载目录，按类型分组；不要直接删除任何文件。",
    trust: { local: "本机", cloud: "不传正文", approval: "删除需审批", rollback: "回收站", estimate: "3-6 分钟" },
    wizard: {
      input: "下载目录",
      preflight: "只读盘点风险",
      output: "分组、空间、预览",
      nextStep: "删除前会停下审批"
    }
  },
  {
    id: "summarize-document",
    title: "总结本地文档",
    summary: "选文件再总结",
    kind: "view",
    view: "files",
    trust: { local: "本机", cloud: "按模式", approval: "只读", rollback: "无改动", estimate: "1-3 分钟" },
    wizard: {
      input: "PDF/DOCX/TXT",
      preflight: "只读预览",
      output: "摘要、重点、来源",
      nextStep: "打开文档工具后选文件"
    }
  },
  {
    id: "find-large-files",
    title: "查找大文件",
    summary: "先列排行，不移动文件",
    kind: "prompt",
    prompt: "找出这台电脑上最大的文件，按安全清理、需确认、建议保留分类；不要直接删除。",
    trust: { local: "本机索引", cloud: "不传路径", approval: "清理需审批", rollback: "先审再处理", estimate: "2-5 分钟" },
    wizard: {
      input: "范围和阈值",
      preflight: "只扫授权目录",
      output: "排行、保留、候选",
      nextStep: "发送后先选文件夹"
    }
  },
  {
    id: "document-qa",
    title: "文档问答",
    summary: "选文件后回答",
    kind: "view",
    view: "files",
    trust: { local: "本机", cloud: "按模式", approval: "只读", rollback: "无改动", estimate: "1-4 分钟" },
    wizard: {
      input: "文档和问题",
      preflight: "读取选中文档",
      output: "带来源回答",
      nextStep: "打开文档工具后提问"
    }
  }
];
