export const IPC_CHANNELS = {
  apiRequest: "mavris:api:request",
  backendStatus: "mavris:backend:status",
  backendStart: "mavris:backend:start",
  backendStop: "mavris:backend:stop",
  backendForeground: "mavris:backend:foreground",
  backendBackground: "mavris:backend:background",
  openExternal: "mavris:shell:open-external",
  chooseSkillDirectory: "mavris:dialog:choose-skill-directory",
  chooseSkillZip: "mavris:dialog:choose-skill-zip",
  browserHostSnapshot: "mavris:browser-host:snapshot",
  browserHostSnapshotChanged: "mavris:browser-host:snapshot-changed",
  browserHostOpen: "mavris:browser-host:open",
  browserHostShow: "mavris:browser-host:show",
  browserHostHide: "mavris:browser-host:hide",
  browserHostSetBounds: "mavris:browser-host:set-bounds",
  browserHostPause: "mavris:browser-host:pause",
  browserHostResume: "mavris:browser-host:resume",
  browserHostTakeover: "mavris:browser-host:takeover",
  browserHostRelease: "mavris:browser-host:release",
  browserHostStop: "mavris:browser-host:stop",
  browserHostAction: "mavris:browser-host:action",
  showNotification: "mavris:show-notification",
  openTaskFromNotification: "mavris:notification:open-task"
} as const;

export type IpcChannel = (typeof IPC_CHANNELS)[keyof typeof IPC_CHANNELS];
