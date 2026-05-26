import { useEffect, useState } from "react";

import { getApprovalDetail, type BackendApproval, type PairingSession } from "./src/api/client";
import { addApprovalNotificationResponseListener, getLastApprovalNotificationApprovalId } from "./src/notifications";
import { ApprovalDetail } from "./src/screens/ApprovalDetail";
import { ApprovalsScreen } from "./src/screens/ApprovalsScreen";
import { PairScreen } from "./src/screens/PairScreen";
import { RemoteScreen } from "./src/screens/RemoteScreen";
import { clearSession, loadSession } from "./src/store/auth";

export default function App() {
  const [session, setSession] = useState<PairingSession | null>(null);
  const [selectedApproval, setSelectedApproval] = useState<BackendApproval | null>(null);
  const [showRemoteScreen, setShowRemoteScreen] = useState(false);

  useEffect(() => {
    void loadSession().then((stored) => {
      if (stored) setSession(stored);
    });
  }, []);

  useEffect(() => {
    if (!session) return undefined;
    const openApprovalFromNotification = (approvalId: string) => {
      void getApprovalDetail(session, approvalId)
        .then((detail) => {
          setShowRemoteScreen(false);
          setSelectedApproval(detail.approval);
        })
        .catch(() => {
          setShowRemoteScreen(false);
        });
    };

    const lastApprovalId = getLastApprovalNotificationApprovalId();
    if (lastApprovalId) openApprovalFromNotification(lastApprovalId);

    const subscription = addApprovalNotificationResponseListener(openApprovalFromNotification);
    return () => subscription.remove();
  }, [session]);

  const handleSessionExpired = () => {
    void clearSession();
    setSelectedApproval(null);
    setShowRemoteScreen(false);
    setSession(null);
  };

  if (!session) {
    return <PairScreen onPaired={setSession} />;
  }

  if (showRemoteScreen) {
    return <RemoteScreen onBack={() => setShowRemoteScreen(false)} onSessionExpired={handleSessionExpired} session={session} />;
  }

  if (selectedApproval) {
    return (
      <ApprovalDetail
        approval={selectedApproval}
        onBack={() => setSelectedApproval(null)}
        onSessionExpired={handleSessionExpired}
        onUpdated={setSelectedApproval}
        session={session}
      />
    );
  }

  return (
    <ApprovalsScreen
      onOpenRemote={() => setShowRemoteScreen(true)}
      onSelectApproval={setSelectedApproval}
      onUnpair={() => {
        setSelectedApproval(null);
        setShowRemoteScreen(false);
        setSession(null);
      }}
      session={session}
    />
  );
}
