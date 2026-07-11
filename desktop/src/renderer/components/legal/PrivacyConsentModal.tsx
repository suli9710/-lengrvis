import { useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";

import type { LegalDocId } from "../../../shared/consent";
import { AccessibleDialog } from "../AccessibleDialog";

interface PrivacyModalProps {
  needsEulaConsent: boolean;
  needsPrivacyConsent: boolean;
  submissionError?: string;
  onAgree: () => void;
  onDecline: () => void;
}

type ConsentDocId = Extract<LegalDocId, "privacy-policy" | "eula">;

const LEGAL_DOC_LABELS: Record<ConsentDocId, string> = {
  "privacy-policy": "隐私政策",
  eula: "最终用户许可协议"
};

const CONSENT_SUMMARY: Array<{ title: string; detail: string }> = [
  {
    title: "本地数据边界",
    detail: "对话、任务和审计记录保存在本机；只有你主动配置云端模型时，所选内容才会发送给对应服务商。"
  },
  {
    title: "遥测默认关闭",
    detail: "当前版本不默认发送崩溃报告或使用统计；诊断包只在你手动点击导出时生成，也不会自动发送。"
  },
  {
    title: "高风险操作需确认",
    detail: "文件修改、远程控制和其他敏感操作受审批、权限策略与桌面原生确认保护。"
  },
  {
    title: "法律文件分别记录",
    detail: "EULA 与隐私政策按各自版本记录同意时间；任一文档更新后都可能要求重新确认。"
  }
];

export function PrivacyConsentModal({
  needsEulaConsent,
  needsPrivacyConsent,
  submissionError,
  onAgree,
  onDecline
}: PrivacyModalProps) {
  const [activeDoc, setActiveDoc] = useState<ConsentDocId | null>(null);
  const [fullDocContent, setFullDocContent] = useState<string | null>(null);
  const [loadingDoc, setLoadingDoc] = useState<ConsentDocId | null>(null);
  const [documentError, setDocumentError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [eulaAccepted, setEulaAccepted] = useState(!needsEulaConsent);
  const [privacyAccepted, setPrivacyAccepted] = useState(!needsPrivacyConsent);
  const fullDocBackRef = useRef<HTMLButtonElement>(null);
  const eulaTriggerRef = useRef<HTMLButtonElement>(null);
  const privacyTriggerRef = useRef<HTMLButtonElement>(null);
  const lastDocTrigger = useRef<ConsentDocId | null>(null);
  const bridge = useMemo(() => {
    return (window as unknown as {
      lengrvis?: {
        consent?: {
          readDoc: (docId: LegalDocId) => Promise<{ content: string; docId: LegalDocId }>;
        };
      };
    }).lengrvis;
  }, []);

  const handleViewFull = useCallback(async (docId: ConsentDocId) => {
    if (loadingDoc || !bridge?.consent?.readDoc) return;
    lastDocTrigger.current = docId;
    setLoadingDoc(docId);
    setDocumentError(null);
    try {
      const { content } = await bridge.consent.readDoc(docId);
      setFullDocContent(content);
      setActiveDoc(docId);
    } catch {
      setDocumentError(`无法加载${LEGAL_DOC_LABELS[docId]}，请稍后重试。`);
    } finally {
      setLoadingDoc(null);
    }
  }, [bridge, loadingDoc]);

  useLayoutEffect(() => {
    if (activeDoc) {
      fullDocBackRef.current?.focus();
      return;
    }
    if (!lastDocTrigger.current) return;
    const trigger = lastDocTrigger.current === "eula" ? eulaTriggerRef.current : privacyTriggerRef.current;
    trigger?.focus();
  }, [activeDoc]);

  const canAgree = eulaAccepted && privacyAccepted;
  const closeDialog = () => {
    if (activeDoc) {
      setActiveDoc(null);
      return;
    }
    onDecline();
  };
  const submitAgreement = async () => {
    if (!canAgree || isSubmitting) return;
    setIsSubmitting(true);
    try {
      await onAgree();
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <AccessibleDialog
      backdropClassName="privacy-consent-overlay"
      className={activeDoc ? "privacy-consent-modal privacy-consent-full-doc" : "privacy-consent-modal"}
      labelledBy={activeDoc ? "privacy-consent-full-title" : "privacy-consent-title"}
      closeDisabled={Boolean(loadingDoc) || isSubmitting}
      onClose={closeDialog}
    >
      {activeDoc ? (
        <>
          <h2 id="privacy-consent-full-title" className="privacy-consent-full-title">{LEGAL_DOC_LABELS[activeDoc]}（完整）</h2>
          <pre className="privacy-consent-full-body">{fullDocContent ?? ""}</pre>
          <div className="privacy-consent-actions">
            <button ref={fullDocBackRef} className="btn btn-secondary" onClick={() => setActiveDoc(null)}>
              返回同意页面
            </button>
          </div>
        </>
      ) : (
        <>
        <div className="privacy-consent-header">
          <h2 id="privacy-consent-title" className="privacy-consent-title">使用条款与隐私说明</h2>
        </div>
        <div className="privacy-consent-body">
          <ul className="privacy-consent-summary-list">
            {CONSENT_SUMMARY.map((item) => (
              <li key={item.title} className="privacy-consent-summary-item">
                <strong>{item.title}</strong>：{item.detail}
              </li>
            ))}
          </ul>
          <div className="privacy-consent-document-links">
            <button
              ref={eulaTriggerRef}
              className="privacy-consent-full-link"
              onClick={() => void handleViewFull("eula")}
              disabled={Boolean(loadingDoc)}
            >
              {loadingDoc === "eula" ? "加载中..." : "查看完整最终用户许可协议"}
            </button>
            <button
              ref={privacyTriggerRef}
              className="privacy-consent-full-link"
              onClick={() => void handleViewFull("privacy-policy")}
              disabled={Boolean(loadingDoc)}
            >
              {loadingDoc === "privacy-policy" ? "加载中..." : "查看完整隐私政策"}
            </button>
          </div>
          <div className="privacy-consent-checkboxes">
            {needsEulaConsent ? (
              <label>
                <input
                  type="checkbox"
                  checked={eulaAccepted}
                  onChange={(event) => setEulaAccepted(event.target.checked)}
                />
                <span>我已阅读并同意最终用户许可协议</span>
              </label>
            ) : null}
            {needsPrivacyConsent ? (
              <label>
                <input
                  type="checkbox"
                  checked={privacyAccepted}
                  onChange={(event) => setPrivacyAccepted(event.target.checked)}
                />
                <span>我已阅读并同意隐私政策</span>
              </label>
            ) : null}
          </div>
          {documentError ? <p className="privacy-consent-error">{documentError}</p> : null}
          {submissionError ? <p className="privacy-consent-error">{submissionError}</p> : null}
        </div>
        <div className="privacy-consent-actions">
          <button className="privacy-consent-decline" onClick={onDecline} disabled={isSubmitting}>
            拒绝并退出
          </button>
          <button className="btn btn-primary" onClick={() => void submitAgreement()} disabled={!canAgree || isSubmitting}>
            {isSubmitting ? "正在保存..." : "同意并开始"}
          </button>
        </div>
        </>
      )}
    </AccessibleDialog>
  );
}
