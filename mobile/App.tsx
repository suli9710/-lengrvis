import { useEffect, useState } from "react";

import type { BackendApproval, PairingSession } from "./src/api/client";
import { ApprovalDetail } from "./src/screens/ApprovalDetail";
import { ApprovalsScreen } from "./src/screens/ApprovalsScreen";
import { PairScreen } from "./src/screens/PairScreen";
import { loadSession } from "./src/store/auth";

export default function App() {
  const [session, setSession] = useState<PairingSession | null>(null);
  const [selectedApproval, setSelectedApproval] = useState<BackendApproval | null>(null);

  useEffect(() => {
    void loadSession().then((stored) => {
      if (stored) setSession(stored);
    });
  }, []);

  if (!session) {
    return <PairScreen onPaired={setSession} />;
  }

  if (selectedApproval) {
    return (
      <ApprovalDetail
        approval={selectedApproval}
        onBack={() => setSelectedApproval(null)}
        onUpdated={setSelectedApproval}
        session={session}
      />
    );
  }

  return (
    <ApprovalsScreen
      onSelectApproval={setSelectedApproval}
      onUnpair={() => {
        setSelectedApproval(null);
        setSession(null);
      }}
      session={session}
    />
  );
}
