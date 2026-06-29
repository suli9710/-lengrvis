import { BottomTabs } from "../src/navigation/BottomTabs";
import { RemoteScreen } from "../src/screens/RemoteScreen";
import { useMobileCompanion } from "../src/state/MobileCompanionContext";

export default function RemoteRoute() {
  const companion = useMobileCompanion();
  return (
    <>
      <RemoteScreen
        grant={companion.remoteInputGrant}
        onBack={() => undefined}
        onRemoteInputGrantRevoked={companion.onRemoteInputGrantRevoked}
        onSessionExpired={companion.onSessionExpired}
        session={companion.session}
      />
      <BottomTabs />
    </>
  );
}
