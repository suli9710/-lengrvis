export const IPC_CHANNELS = {
  apiRequest: "mavris:api:request",
  backendStatus: "mavris:backend:status",
  backendStart: "mavris:backend:start",
  backendStop: "mavris:backend:stop",
  openExternal: "mavris:shell:open-external",
  chooseSkillDirectory: "mavris:dialog:choose-skill-directory",
  chooseSkillZip: "mavris:dialog:choose-skill-zip",
  showNotification: "mavris:notification:show"
} as const;

export type IpcChannel = (typeof IPC_CHANNELS)[keyof typeof IPC_CHANNELS];
