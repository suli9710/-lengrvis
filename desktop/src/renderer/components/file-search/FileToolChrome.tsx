import { fileOnboardingHeadline, type FileOnboardingStep, type FileToolTabValue } from "./FileSearchModels";
import { Badge } from "../Panel";

export function FileServiceGate() {
  return (
    <section className="file-service-gate" aria-label="Lengrvis 服务连接提示">
      <div>
        <strong>助手暂时连不上，电脑和文件夹没有问题</strong>
        <p>文件搜索、范围保存和文档读取需要本机服务参与。请先点右上角刷新，或到设置里启动服务；连接恢复后，你已填的关键词和路径还可以继续用。</p>
      </div>
      <Badge tone="warning">先恢复连接</Badge>
    </section>
  );
}

export function FileOnboardingRail({
  steps,
  onSelectTool
}: {
  steps: FileOnboardingStep[];
  onSelectTool: (tool: FileToolTabValue) => void;
}) {
  return (
    <section className="file-onboarding-rail" aria-label="文件工具开箱流程">
      <div className="file-onboarding-rail__copy">
        <span>首次任务流</span>
        <strong>{fileOnboardingHeadline(steps)}</strong>
      </div>
      <div className="file-onboarding-steps">
        {steps.map((step, index) => (
          <button
            key={step.id}
            type="button"
            className={`file-onboarding-step file-onboarding-step--${step.state}`}
            onClick={() => onSelectTool(step.tool)}
            aria-current={step.state === "current" ? "step" : undefined}
          >
            <span className="file-onboarding-step__index">{index + 1}</span>
            <span className="file-onboarding-step__label">{step.label}</span>
          </button>
        ))}
      </div>
    </section>
  );
}
