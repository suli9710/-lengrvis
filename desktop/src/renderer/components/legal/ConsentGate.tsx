import type { ReactNode } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import type { ConsentRecord, ConsentStatusResult } from "../../../shared/consent";
import { isWebOnlyDevConsentGateBypassEnabled } from "../../lib/api/transport";
import { AccessibleDialog } from "../AccessibleDialog";
import { PrivacyConsentModal } from "./PrivacyConsentModal";

type ConsentBridge = {
  consent?: {
    getStatus: () => Promise<ConsentStatusResult>;
    accept: (request: { acceptPrivacy?: boolean; acceptEula?: boolean; installerVersion?: string }) => Promise<ConsentRecord>;
  };
};

interface ConsentGateProps {
  children: ReactNode;
}

export function ConsentGate({ children }: ConsentGateProps) {
  const [consentStatus, setConsentStatus] = useState<ConsentStatusResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [submissionError, setSubmissionError] = useState("");
  const retryButtonRef = useRef<HTMLButtonElement>(null);
  const [statusError, setStatusError] = useState("");

  useEffect(() => {
    let isActive = true;
    const bridge = (window as unknown as { lengrvis?: ConsentBridge }).lengrvis;
    if (!bridge?.consent?.getStatus) {
      if (isWebOnlyDevConsentGateBypassEnabled()) {
        setLoading(false);
        return;
      }
      setStatusError(
        "无法连接桌面安全桥接，无法验证使用条款与隐私同意。请使用 Lengrvis 桌面客户端；若仅在 dev:web 本地调试，可显式设置 VITE_LENGRVIS_DEV_SKIP_CONSENT_GATE=true。"
      );
      setLoading(false);
      return;
    }
    bridge.consent.getStatus()
      .then((status) => {
        if (!isActive) return;
        setConsentStatus(status);
        setShowModal(status.needsEulaConsent || status.needsPrivacyConsent);
        setLoading(false);
      })
      .catch(() => {
        if (!isActive) return;
        setStatusError("无法读取本机同意记录。应用不会在法律状态未知时继续，请重试或退出。");
        setLoading(false);
      });
    return () => {
      isActive = false;
    };
  }, []);

  const handleAgree = useCallback(async () => {
    const bridge = (window as unknown as { lengrvis?: ConsentBridge }).lengrvis;
    if (!bridge?.consent?.accept || !consentStatus) return;
    setSubmissionError("");
    try {
      const record = await bridge.consent.accept({
        acceptEula: consentStatus.needsEulaConsent,
        acceptPrivacy: consentStatus.needsPrivacyConsent
      });
      if (
        (consentStatus.needsEulaConsent && !record.eula_accepted_at)
        || (consentStatus.needsPrivacyConsent && !record.privacy_accepted_at)
      ) {
        throw new Error("Consent record was not persisted");
      }
      setShowModal(false);
    } catch {
      setSubmissionError("无法保存同意记录。为保护你的选择，应用将保持在此页面，请重试或退出。");
    }
  }, [consentStatus]);

  const handleDecline = useCallback(() => {
    window.close();
  }, []);

  if (loading) {
    return null;
  }

  if (statusError) {
    return (
      <AccessibleDialog
        role="alertdialog"
        backdropClassName="privacy-consent-overlay"
        className="privacy-consent-modal"
        labelledBy="consent-status-error-title"
        describedBy="consent-status-error-description"
        closeDisabled
        initialFocusRef={retryButtonRef}
        onClose={handleDecline}
      >
          <div className="privacy-consent-header">
            <h2 id="consent-status-error-title" className="privacy-consent-title">无法验证使用条款</h2>
          </div>
          <div className="privacy-consent-body">
            <p id="consent-status-error-description" className="privacy-consent-error">{statusError}</p>
          </div>
          <div className="privacy-consent-actions">
            <button className="privacy-consent-decline" onClick={handleDecline}>
              退出
            </button>
            <button ref={retryButtonRef} className="btn btn-primary" onClick={() => window.location.reload()}>
              重试
            </button>
          </div>
      </AccessibleDialog>
    );
  }

  if (showModal) {
    return (
      <PrivacyConsentModal
        needsEulaConsent={Boolean(consentStatus?.needsEulaConsent)}
        needsPrivacyConsent={Boolean(consentStatus?.needsPrivacyConsent)}
        submissionError={submissionError}
        onAgree={handleAgree}
        onDecline={handleDecline}
      />
    );
  }

  return <>{children}</>;
}
