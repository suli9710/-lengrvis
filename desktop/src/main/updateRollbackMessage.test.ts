import { describe, expect, it } from "vitest";

import { buildUpdateRecoveryMessage } from "./updateRollbackMessage";

describe("buildUpdateRecoveryMessage", () => {
  it("describes quarantine and manual recovery without claiming an automatic rollback", () => {
    const message = buildUpdateRecoveryMessage("1.2.0", "1.1.0");

    expect(message.title).toBe("更新已隔离");
    expect(message.message).toContain("已暂停自动安装");
    expect(message.detail).toContain("手动恢复到上一个稳定版本 1.1.0");
    expect(JSON.stringify(message)).not.toContain("已回滚");
  });
});
