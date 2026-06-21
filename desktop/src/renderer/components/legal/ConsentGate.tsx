import { useCallback, useEffect, useState } from "react";

import type { ConsentStatusResult } from "../../../shared/consent";
import { PrivacyConsentModal } from "./PrivacyConsentModal";

type Bridge = {
  consent?: {
    getStatus: () => Promise<ConsentStatusResult>;
    accept: (request: { acceptPrivacy?: boolean; acceptEula?: boolean; installerVersion?: string }) => Promise<unknown>;
  };
};

interface ConsentGateProps {
  children: React.ReactNode;
  appVersion: string;
}

/**
 * Wrap the entire app. On mount, check consent status.
 * If privacy consent is missing, show the modal gate.
 * Once accepted, render children. If declined, Quit via electron.
 */
export function ConsentGate({ children, appVersion }: ConsentGateProps) {
  const [consentStatus, setConsentStatus] = useState<ConsentStatusResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);

  useEffect(() => {
    let isActive = true;
    const bridge = (window as unknown as { lengrvis?: Bridge }).lengrvis;
    if (!bridge?.consent?.getStatus) {
      setLoading(false);
      return;
    }
    bridge.consent.getStatus()
      .then((status) => {
        if (!isActive) return;
        setConsentStatus(status);
        setShowModal(status.needsPrivacyConsent);
        setLoading(false);
      })
      .catch(() => {
        if (!isActive) return;
        setLoading(false);
      });
    return () => { isActive = false; };
  }, []);

  const handleAgree = useCallback(async () => {
    const bridge = (window as unknown as { lengrvis?: Bridge }).lengrvis;
    if (!bridge?.consent?.accept) return;
    try {
      await bridge.consent.accept({ acceptPrivacy: true });
      setShowModal(false);
    } catch {
      // If consent write fails, still proceed — user explicitly agreed.
      setShowModal(false);
    }
  }, []);

  const handleDecline = useCallback(() => {
    // Sending an empty query to the backend to trigger graceful shutdown.
    // Alternatively, close the window.
    window.close();
  }, []);

  if (loading) {
    return null;
  }

  if (showModal) {
    return <PrivacyConsentModal onAgree={handleAgree} onDecline={handleDecline} />;
  }

  return <>{children}</>;
}

type Bridge = {
  consent?: {
    getStatus: () => Promise<ConsentStatusResult>;
    accept: (request: { acceptPrivacy?: boolean; acceptEula?: boolean; installerVersion?: string }) => Promise<unknown>;
  };
};
