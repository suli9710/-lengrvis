export interface UpdateRecoveryMessage {
  title: string;
  message: string;
  detail: string;
}

export function buildUpdateRecoveryMessage(
  quarantinedVersion: string | null,
  lastGoodVersion: string | null
): UpdateRecoveryMessage {
  const detailLines = [
    quarantinedVersion
      ? `版本 ${quarantinedVersion} 连续启动失败，已隔离该版本并停用自动安装。`
      : "最近一次更新连续启动失败，已隔离并停用自动安装。",
    lastGoodVersion
      ? `请从历史版本页面手动恢复到上一个稳定版本 ${lastGoodVersion}。`
      : "请从历史版本页面手动重新安装稳定版本。"
  ];
  return {
    title: "更新已隔离",
    message: "检测到更新后启动异常，已暂停自动安装。",
    detail: detailLines.join("\n")
  };
}
