# 4开发 + 4审核 — Sprint P2 收尾报告

**日期：** 2026-06-11  
**范围：** P1 报告「仍待跟进」项 + 审核合并

---

## 本轮实现

| 项 | 文件 | 改动 |
|----|------|------|
| Desktop DPAPI fail-closed | `desktop/src/main/desktopApiToken.ts` | 解密/读盘失败抛 `DesktopApiTokenPersistError`；禁止 `memory` fallback |
| dev:web WS token | `desktop/src/renderer/lib/apiClient.ts` | 无 token 时阻断 WS；有 token 时注入 `lengrvis.desktop.token.*` subprotocol |
| 混合完成语义 | `step_scheduler_handler.py` | 成功 step + blocked skip → `FAILED` |
| Model manifest 钉扎 | `model_manifest.json` | Xenova 模型 40 位 sha；Qwen/OCR 改 `recommended: false` 待手动安装 |
| Install 脚本校验 | `install_acceleration.ps1` | 拒绝 `main`/非 40 位 hex revision |

## 新增测试

- `backend/tests/test_skipped_completion_semantics.py`

## 验证

```
backend: test_skipped_completion_semantics + parallel + permission — 18 passed
desktop: build:electron + desktop-api-token-lifecycle-smoke — passed
```

## 仍待（PR-A / PR-D）

- `OrchestratorRegistry` / `run_service` 按 run 注入 engine
- `agent_bus` 实例级订阅表
- Desktop env vs file token 优先级文档化
- Qwen ONNX GenAI 公共 repo + sha 确认后恢复 `recommended: true`
