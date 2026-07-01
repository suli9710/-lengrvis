import { lazy, Suspense } from "react";

import { ChatPanel } from "../../components/ChatPanel";
import { RouteLoading } from "../../appViewModel";
import type { AppSurfaceProps } from "../AppSurfaceTypes";

const FileSearchPanel = lazy(() => import("../../components/FileSearchPanel").then((module) => ({ default: module.FileSearchPanel })));

type FilesRouteProps = Pick<
  AppSurfaceProps,
  | "api"
  | "connectionState"
  | "documentIntent"
  | "fileResults"
  | "fileSearchError"
  | "fileSearchMeta"
  | "fileToolTab"
  | "isSearching"
  | "pendingApproval"
  | "recentReadableMessages"
  | "settings"
  | "onClearFileResults"
  | "onDocumentIntentHandled"
  | "onFileToolChange"
  | "onOpenTaskApproval"
  | "onRequestCleanupApproval"
  | "onSaveSettings"
  | "onSearchFiles"
  | "onSendMessage"
>;

export function FilesRoute({
  api,
  connectionState,
  documentIntent,
  fileResults,
  fileSearchError,
  fileSearchMeta,
  fileToolTab,
  isSearching,
  pendingApproval,
  recentReadableMessages,
  settings,
  onClearFileResults,
  onDocumentIntentHandled,
  onFileToolChange,
  onOpenTaskApproval,
  onRequestCleanupApproval,
  onSaveSettings,
  onSearchFiles,
  onSendMessage
}: FilesRouteProps) {
  return (
    <section className="detail-grid">
      <Suspense fallback={<RouteLoading />}>
        <FileSearchPanel
          results={fileResults}
          searchMeta={fileSearchMeta}
          isSearching={isSearching}
          onSearch={onSearchFiles}
          onClearResults={onClearFileResults}
          searchError={fileSearchError}
          api={api}
          connectionState={connectionState}
          settings={settings}
          onSaveSettings={onSaveSettings}
          initialTool={fileToolTab}
          onToolChange={onFileToolChange}
          selectedDocumentPath={documentIntent?.path}
          selectedDocumentAction={documentIntent?.action}
          selectedDocumentIntentId={documentIntent?.nonce}
          onDocumentIntentHandled={onDocumentIntentHandled}
          hasPendingApproval={Boolean(pendingApproval)}
          onOpenApprovals={onOpenTaskApproval}
          onRequestCleanupApproval={onRequestCleanupApproval}
        />
      </Suspense>
      <ChatPanel messages={recentReadableMessages} connectionState={connectionState} onSend={onSendMessage} api={api} />
    </section>
  );
}
