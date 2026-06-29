import { BottomTabs } from "../src/navigation/BottomTabs";
import { WakeupsScreen } from "../src/screens/WakeupsScreen";
import { useMobileCompanion } from "../src/state/MobileCompanionContext";

export default function WakeupsRoute() {
  const companion = useMobileCompanion();
  return (
    <>
      <WakeupsScreen
        onBack={() => undefined}
        onSessionExpired={companion.onSessionExpired}
        session={companion.session}
      />
      <BottomTabs />
    </>
  );
}
