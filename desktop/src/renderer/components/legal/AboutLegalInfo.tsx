import { useCallback, useEffect, useMemo, useState } from "react";

import type { ConsentStatusResult, LegalDocId } from "../../../shared/consent";

type ConsentStatus = ConsentStatusResult;

/**
 * Settings → About → Legal Information section.
 *
 * Displays the three legal documents as clickable links,
 * shows the currently consented versions and timestamps.
 */

interface AboutLegalProps {
  appVersion: string;
}

const LEGAL_DOC_LABELS: Record<LegalDocId, string> = {
  "privacy-policy": "\u9690\u79c1\u653f\u7b56",
  "eula": "\u6700\u7ec8\u7528\u6237\u8bb8\u53ef\u534f\u8bae",
  "notice": "\u7b2c\u4e09\u65b9\u8bb8\u53ef\u58f0\u660e"
};

export function AboutLegalInfo({ appVersion }: AboutLegalProps) {
  const [consentStatus, setConsentStatus] = useState<ConsentStatus | null>(null);
  const [activeDoc, setActiveDoc] = useState<LegalDocId | null>(null);
  const [docContent, setDocContent] = useState<string | null>(null);
  const [loadingDoc, setLoadingDoc] = useState<LegalDocId | null>(null);
  const [error, setError] = useState<string | null>(null);
  const bridge = useMemo(() => {
    return (window as unknown as { lengrvis?: { consent?: { getStatus: () => Promise<ConsentStatusResult>; readDoc: (docId: LegalDocId) => Promise<{ content: string; docId: LegalDocId }> } } }).lengrvis;
  }, []);

  useEffect(() => {
    if (!bridge?.consent?.getStatus) return;
    let isActive = true;
    bridge.consent.getStatus()
      .then((status) => {
        if (isActive) setConsentStatus(status);
      })
      .catch(() => undefined);
    return () => { isActive = false; };
  }, [bridge]);

  const handleOpenDoc = useCallback(async (docId: LegalDocId) => {
    if (loadingDoc || !bridge?.consent?.readDoc) return;
    setLoadingDoc(docId);
    setError(null);
    try {
      const { content } = await bridge.consent.readDoc(docId);
      setDocContent(content);
      setActiveDoc(docId);
    } catch {
      setError(`\u65e0\u6cd5\u52a0\u8f7d ${LEGAL_DOC_LABELS[docId]}`);
    } finally {
      setLoadingDoc(null);
    }
  }, [bridge, loadingDoc]);

  const formatDate = (iso: string | null): string => {
    if (!iso) return "\u672a\u540c\u610f";
    try {
      const d = new Date(iso);
      return d.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" });
    } catch {
      return iso;
    }
  };

  if (activeDoc) {
    return (
      <div className="about-legal-doc-viewer">
        <div className="about-legal-doc-header">
          <button className="btn btn-secondary" onClick={() => { setActiveDoc(null); setDocContent(null); }}>
            \u8fd4\u56de
          </button>
          <h3 className="about-legal-doc-title">{LEGAL_DOC_LABELS[activeDoc]}</h3>
        </div>
        {error ? (
          <p className="about-legal-error">{error}</p>
        ) : (
          <pre className="about-legal-doc-body">{docContent ?? ""}</pre>
        )}
      </div>
    );
  }

  const consent = consentStatus?.consent;
  return (
    <div className="about-legal-info">
      <h3 className="about-legal-title">\u6cd5\u5f8b\u4fe1\u606f</h3>
      <div className="about-legal-links">
        {(Object.keys(LEGAL_DOC_LABELS) as LegalDocId[]).map((docId) => (
          <button
            key={docId}
            className="about-legal-link"
            onClick={() => handleOpenDoc(docId)}
            disabled={loadingDoc === docId}
          >
            {loadingDoc === docId ? "\u52a0\u8f7d\u4e2d..." : LEGAL_DOC_LABELS[docId]}
          </button>
        ))}
      </div>
      {consent && (
        <div className="about-legal-consent-info">
          <p className="about-legal-consent-versions">
            \u5f53\u524d\u5df2\u540c\u610f\u7248\u672c\uff1aEULA {consent.eula_version} / \u9690\u79c1\u653f\u7b56 {consent.privacy_version}
          </p>
          <p className="about-legal-consent-times">
            EULA \u540c\u610f\u65f6\u95f4\uff1a{formatDate(consent.eula_accepted_at)}
            <br />
            \u9690\u79c1\u653f\u7b56\u540c\u610f\u65f6\u95f4\uff1a{formatDate(consent.privacy_accepted_at)}
          </p>
        </div>
      )}
      {error && <p className="about-legal-error">{error}</p>}
    </div>
  );
}
