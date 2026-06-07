import { useEffect, useState } from "react";

import { getApprovalDetail, type BackendApproval, type PairingSession, type RemoteInputGrant } from "./src/api/client";
import { addApprovalNotificationResponseListener, getLastApprovalNotificationApprovalId } from "./src/notifications";
import { ApprovalDetail } from "./src/screens/ApprovalDetail";
import { ApprovalsScreen } from "./src/screens/ApprovalsScreen";
import { PairScreen } from "./src/screens/PairScreen";
import { RemoteScreen } from "./src/screens/RemoteScreen";
import { isRemoteInputGrantUsable, reduceRemoteInputGrant, remoteInputGrantExpiryDelayMs } from "./src/remoteInputGrant";
import { clearSession, loadSession } from "./src/store/auth";

type ActiveScreen = "approvals" | "remote";

export default function App() {
  const [session, setSession] = useState<PairingSession | null>(null);
  const [selectedApproval, setSelectedApproval] = useState<BackendApproval | null>(null);
  const [activeScreen, setActiveScreen] = useState<ActiveScreen>("approvals");
  const [remoteInputGrant, setRemoteInputGrant] = useState<RemoteInputGrant | null>(null);

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
          setSelectedApproval(detail.approval);
        })
        .catch(() => undefined);
    };

    const lastApprovalId = getLastApprovalNotificationApprovalId();
    if (lastApprovalId) openApprovalFromNotification(lastApprovalId);

    const subscription = addApprovalNotificationResponseListener(openApprovalFromNotification);
    return () => subscription.remove();
  }, [session]);

  useEffect(() => {
    if (!remoteInputGrant) return undefined;
    if (!isRemoteInputGrantUsable(remoteInputGrant)) {
      setRemoteInputGrant(null);
      return undefined;
    }
    const remainingMs = remoteInputGrantExpiryDelayMs(remoteInputGrant);
    if (remainingMs === null) {
      setRemoteInputGrant(null);
      return undefined;
    }
    const grantId = remoteInputGrant.id;
    const timeout = setTimeout(() => {
      setRemoteInputGrant((current) => reduceRemoteInputGrant(current, { type: "expired", grantId }));
    }, remainingMs);
    return () => clearTimeout(timeout);
  }, [remoteInputGrant]);

  const handleRemoteInputGrant = (grant: RemoteInputGrant) => {
    setRemoteInputGrant((current) => reduceRemoteInputGrant(current, { type: "received", grant }));
  };

  const handleRemoteInputGrantRevoked = (grant: RemoteInputGrant) => {
    setRemoteInputGrant((current) => reduceRemoteInputGrant(current, { type: "revoked", grantId: grant.id }));
  };

  const handleSessionExpired = () => {
    void clearSession();
    setSelectedApproval(null);
    setRemoteInputGrant((current) => reduceRemoteInputGrant(current, { type: "cleared" }));
    setActiveScreen("approvals");
    setSession(null);
  };

  if (!session) {
    return <PairScreen onPaired={setSession} />;
  }

  if (activeScreen === "remote") {
    return (
      <RemoteScreen
        grant={remoteInputGrant}
        onBack={() => setActiveScreen("approvals")}
        onRemoteInputGrantRevoked={handleRemoteInputGrantRevoked}
        session={session}
      />
    );
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
      onOpenRemote={() => setActiveScreen("remote")}
      onRemoteInputGrant={handleRemoteInputGrant}
      onRemoteInputGrantRevoked={handleRemoteInputGrantRevoked}
      onSelectApproval={setSelectedApproval}
      onUnpair={() => {
        setSelectedApproval(null);
        setRemoteInputGrant((current) => reduceRemoteInputGrant(current, { type: "cleared" }));
        setActiveScreen("approvals");
        setSession(null);
      }}
      session={session}
    />
  );
}
