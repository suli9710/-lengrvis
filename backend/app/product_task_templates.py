from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskStarterTemplate:
    id: str
    title: str
    summary: str
    input_hint: str
    preflight: tuple[str, ...]
    local_boundary: str
    cloud_boundary: str
    approval: str
    rollback: str
    estimate: str
    output_type: str
    prompt: str

    def __getitem__(self, key: str) -> str:
        if key == "label":
            return self.title
        if key == "prompt":
            return self.prompt
        raise KeyError(key)


TASK_STARTER_TEMPLATES: tuple[TaskStarterTemplate, ...] = (
    TaskStarterTemplate(
        id="clean-downloads",
        title="整理下载目录",
        summary="先盘点、分组、生成清理计划，删除前必须审批。",
        input_hint="选择或确认下载目录，也可以直接使用默认 Downloads。",
        preflight=("目录可访问", "文件索引可用", "删除动作会先生成预览"),
        local_boundary="本机文件名、大小、类型和路径在本机处理。",
        cloud_boundary="默认不上传文件正文；混合模式只允许上传脱敏计划。",
        approval="移动或桌面审批后才会移动、删除或改名。",
        rollback="回收站/移动类动作保留回滚入口。",
        estimate="3-6 分钟",
        output_type="清理计划",
        prompt=(
            "扫描我的下载目录，按安装包、文档、图片、压缩包、大文件和临时文件分组，"
            "给出整理建议和可回滚清理计划；不要直接删除或移动任何文件。"
        ),
    ),
    TaskStarterTemplate(
        id="summarize-document",
        title="总结本地文档",
        summary="选择文档后生成摘要、要点和引用来源。",
        input_hint="选择 PDF、DOCX、TXT、PPTX、XLSX 或 CSV。",
        preflight=("文档在授权目录内", "文本/OCR 可抽取", "引用来源可展示"),
        local_boundary="文档抽取、OCR 和引用定位优先留在本机。",
        cloud_boundary="快速/混合模式可能上传必要片段；隐私模式不上云。",
        approval="只读任务不需要修改审批。",
        rollback="不修改文件，无需回滚。",
        estimate="1-3 分钟",
        output_type="带引用摘要",
        prompt="总结我选择的本地文档，列出关键结论、待办事项和引用来源；不要修改原文件。",
    ),
    TaskStarterTemplate(
        id="find-large-files",
        title="查找大文件",
        summary="找出占空间文件并输出可导出的清单。",
        input_hint="选择扫描范围，或使用已授权目录。",
        preflight=("扫描范围已授权", "只读取文件元数据", "清理前会二次确认"),
        local_boundary="文件路径、大小和修改时间在本机处理。",
        cloud_boundary="默认不上传路径；混合模式只上传脱敏统计。",
        approval="清理建议需要审批后才能执行。",
        rollback="移动到回收站或指定目录时可回滚。",
        estimate="2-5 分钟",
        output_type="大文件表格",
        prompt=(
            "找出这台电脑授权范围内最大的文件，按安全清理、需要确认、建议保留三类输出，"
            "并说明原因；不要直接删除任何文件。"
        ),
    ),
    TaskStarterTemplate(
        id="check-computer",
        title="检查电脑状态",
        summary="只读检查后端、系统、磁盘、进程和本地 AI 状态。",
        input_hint="无需输入，点击即可开始只读检查。",
        preflight=("后端健康检查", "系统信息只读", "不会改动设置"),
        local_boundary="系统状态、磁盘和进程信息只在本机读取。",
        cloud_boundary="不上云。",
        approval="只读任务不需要审批。",
        rollback="不修改系统，无需回滚。",
        estimate="30 秒",
        output_type="电脑体检卡",
        prompt="检查这台电脑的运行状态、磁盘、内存、关键进程和本地 AI 可用性，只读输出建议。",
    ),
    TaskStarterTemplate(
        id="document-qa",
        title="文档问答",
        summary="选择本地文档后带来源回答问题。",
        input_hint="选择文档并输入一个具体问题。",
        preflight=("文档在授权目录内", "问题已填写", "答案必须带来源"),
        local_boundary="文档抽取、chunk、embedding 和引用优先留本机。",
        cloud_boundary="快速/混合模式可能上传必要片段；隐私模式不上云。",
        approval="只读任务不需要修改审批。",
        rollback="不修改文件，无需回滚。",
        estimate="1-4 分钟",
        output_type="带引用问答",
        prompt="基于我选择的本地文档回答问题，必须给出引用来源；不要修改原文件。",
    ),
)


def list_task_starter_templates() -> list[dict[str, object]]:
    return [
        {
            "id": item.id,
            "title": item.title,
            "summary": item.summary,
            "input_hint": item.input_hint,
            "preflight": list(item.preflight),
            "trust": {
                "local": item.local_boundary,
                "cloud": item.cloud_boundary,
                "approval": item.approval,
                "rollback": item.rollback,
                "estimate": item.estimate,
            },
            "output_type": item.output_type,
        }
        for item in TASK_STARTER_TEMPLATES
    ]


def task_starter_prompt(template_id: str, user_input: str = "") -> str:
    template = get_task_starter_template(template_id)
    user_input = user_input.strip()
    if not user_input:
        return template.prompt
    return f"{template.prompt}\n\n用户补充：{user_input}"


def get_task_starter_template(template_id: str) -> TaskStarterTemplate:
    for template in TASK_STARTER_TEMPLATES:
        if template.id == template_id:
            return template
    raise KeyError(template_id)
