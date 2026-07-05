import { useCallback, type Dispatch, type MutableRefObject, type SetStateAction } from "react";

import type { ChatMessage } from "../../shared/catalogTypes";
import type { RealtimeConnectionStatus } from "../lib/apiClient";
import {
  appendUniqueMessage,
  realtimeStatusChatMessage,
  shouldShowRealtimeStatusMessage,
  upsertRealtimeBadMessageNotice,
  type RealtimeBadMessageNotice
} from "../appViewModel";

interface RealtimeStatusHandlersOptions {
  setRealtimeStatus: (status: RealtimeConnectionStatus) => void;
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  realtimeBadMessageNotice: MutableRefObject<RealtimeBadMessageNotice>;
}

export function useRealtimeStatusHandlers({
  setRealtimeStatus,
  setMessages,
  realtimeBadMessageNotice
}: RealtimeStatusHandlersOptions) {
  const handleRealtimeStatus = useCallback(
    (status: RealtimeConnectionStatus) => {
      setRealtimeStatus(status);
      if (shouldShowRealtimeStatusMessage(status)) {
        setMessages((current) => appendUniqueMessage(current, realtimeStatusChatMessage(status)));
      }
    },
    [setMessages, setRealtimeStatus]
  );

  const handleRealtimeBadMessage = useCallback(
    (status: RealtimeConnectionStatus & { state: "bad_message"; rawMessage: string }) => {
      setRealtimeStatus(status);
      setMessages((current) => upsertRealtimeBadMessageNotice(current, realtimeBadMessageNotice.current, status));
    },
    [realtimeBadMessageNotice, setMessages, setRealtimeStatus]
  );

  return { handleRealtimeStatus, handleRealtimeBadMessage };
}
