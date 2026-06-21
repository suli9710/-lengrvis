import { useCallback, useEffect, useMemo, useState } from "react";

import type { LegalDocId, ConsentStatusResult } from "../../../shared/consent";

/**
 * First-launch privacy policy modal.
 *
 * Shows 5 key-point takeaways with a link to read the full policy.
 * "Agree and start" records consent; "Decline and exit" quits the app.
 */

interface PrivacyModalProps {
  /** Called after the user agrees to the privacy policy. */
  onAgree: () => void;
  /** Called when the user declines — typically ends the session. */
  onDecline: () => void;
}

const PRIVACY_SUMMARY: Array<{ title: string; detail: string }> = [
  {
    title: "\u60a8\u7684\u6570\u636e\u7559\u5728\u672c\u5730",
    detail: "\u5bf9\u8bdd\u3001\u4efb\u52a1\u3001\u5ba1\u8ba1\u65e5\u5fd7\u5747\u5b58\u50a8\u5728\u60a8\u7684\u8bbe\u5907\u4e0a\uff0c\u4e0d\u4e0a\u4f20\u81f3\u4efb\u4f55\u670d\u52a1\u5668"
  },
  {
    title: "\u533f\u540d\u9065\u6d4b\u9ed8\u8ba4\u5173\u95ed",
    detail: "\u5d29\u6e83\u62a5\u544a\u548c\u4f7f\u7528\u7edf\u8ba1\u4ec5\u5728\u60a8\u4e3b\u52a8\u5f00\u542f\u65f6\u624d\u53d1\u9001\uff0c\u4e14\u5b8c\u5168\u533f\u540d"
  },
  {
    title: "\u4e91\u7aef\u6a21\u578b\u53ef\u9009",
    detail: "\u4ec5\u5f53\u60a8\u9009\u62e9\u4e91\u7aef LLM \u65f6\uff0c\u5bf9\u8bdd\u5185\u5bb9\u624d\u53d1\u9001\u81f3\u5bf9\u5e94\u670d\u52a1\u5546"
  },
  {
    title: "\u8fdc\u7a0b\u63a7\u5236\u9700\u5ba1\u6279",
    detail: "\u79fb\u52a8\u7aef\u8fdc\u7a0b\u64cd\u4f5c\u987b\u7ecf\u684c\u9762\u7aef\u660e\u786e\u6279\u51c6\uff0c\u5168\u90e8\u8bb0\u5f55\u5728\u5ba1\u8ba1\u65e5\u5fd7\u4e2d"
  },
  {
    title: "\u968f\u65f6\u53ef\u5220\u9664",
    detail: "\u60a8\u53ef\u968f\u65f6\u5728\u8bbe\u7f6e\u4e2d\u4e00\u952e\u6e05\u9664\u6240\u6709\u672c\u5730\u6570\u636e"
  }
];

export function PrivacyConsentModal({ onAgree, onDecline }: PrivacyModalProps) {
  const [showFullDoc, setShowFullDoc] = useState(false);
  const [fullDocContent, setFullDocContent] = useState<string | null>(null);
  const [loadingDoc, setLoadingDoc] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bridge = useMemo(() => {
    return (window as unknown as { lengrvis?: { consent?: { readDoc: (docId: LegalDocId) => Promise<{ content: string; docId: LegalDocId }> } } }).lengrvis;
  }, []);

  const handleViewFull = useCallback(async () => {
    if (loadingDoc || !bridge?.consent?.readDoc) return;
    setLoadingDoc(true);
    setError(null);
    try {
      const { content } = await bridge.consent.readDoc("privacy-policy");
      setFullDocContent(content);
      setShowFullDoc(true);
    } catch {
      setError("\u65e0\u6cd5\u52a0\u8f7d\u9690\u79c1\u653f\u7b56\u6587\u4ef6\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002");
    } finally {
      setLoadingDoc(false);
    }
  }, [bridge, loadingDoc]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !showFullDoc) {
        onDecline();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onDecline, showFullDoc]);

  if (showFullDoc) {
    return (
      <div className="privacy-consent-overlay">
        <div className="privacy-consent-modal privacy-consent-full-doc">
          <h2 className="privacy-consent-full-title">\u9690\u79c1\u653f\u7b56\uff08\u5b8c\u6574\uff09</h2>
          <pre className="privacy-consent-full-body">{fullDocContent ?? ""}</pre>
          <div className="privacy-consent-actions">
            <button className="btn btn-secondary" onClick={() => setShowFullDoc(false)}>
              \u8fd4\u56de\u6458\u8981
            </button>
            <button
              className="btn btn-primary"
              onClick={() => {
                setShowFullDoc(false);
                onAgree();
              }}
            >
              \u540c\u610f\u5e76\u5f00\u59cb
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="privacy-consent-overlay">
      <div className="privacy-consent-modal" role="dialog" aria-modal="true" aria-labelledby="privacy-consent-title">
        <div className="privacy-consent-header">
          <span className="privacy-consent-icon" aria-hidden="true">\U0001F512</span>
          <h2 id="privacy-consent-title" className="privacy-consent-title">\u9690\u79c1\u653f\u7b56</h2>
        </div>
        <div className="privacy-consent-body">
          <ul className="privacy-consent-summary-list">
            {PRIVACY_SUMMARY.map((item, index) => (
              <li key={index} className="privacy-consent-summary-item">
                <strong>{item.title}</strong> — {item.detail}
              </li>
            ))}
          </ul>
          <button
            className="privacy-consent-full-link"
            onClick={handleViewFull}
            disabled={loadingDoc}
            aria-label="\u67e5\u770b\u5b8c\u6574\u9690\u79c1\u653f\u7b56"
          >
            {loadingDoc ? "\u52a0\u8f7d\u4e2d..." : "\u67e5\u770b\u5b8c\u6574\u9690\u79c1\u653f\u7b56"}
          </button>
          {error && <p className="privacy-consent-error">{error}</p>}
        </div>
        <div className="privacy-consent-actions">
          <button className="privacy-consent-decline" onClick={onDecline} aria-label="\u62d2\u7edd\u5e76\u9000\u51fa">
            \u62d2\u7edd\u5e76\u9000\u51fa
          </button>
          <button className="btn btn-primary" onClick={onAgree} aria-label="\u540c\u610f\u5e76\u5f00\u59cb">
            \u540c\u610f\u5e76\u5f00\u59cb
          </button>
        </div>
      </div>
    </div>
  );
}
