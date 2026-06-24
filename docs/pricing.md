# 定价与套餐设计 (Free / Pro / Team-Self-hosted)

> 本文档定义 lengrvis 的商业化套餐分层、各档能力边界，以及高风险能力（手机远控）与付费层 + 强审批的绑定关系。
> 套餐通过环境变量 `LENGRVIS_PLAN`（`free` / `pro` / `team`）选择，后端以 feature gating 落地（见 `backend/app/commerce/entitlements.py`）。
> 状态：内部产品与技术基线，不构成公开报价或购买要约。价格、税务、支付渠道和合同条款在 `docs/business/market-readiness.md` 清零前不得对外承诺。

## 1. 套餐总览

| 套餐 | 定位 | 部署形态 | 目标用户 |
| --- | --- | --- | --- |
| **Free** | 本机只读 + 基础任务 | 本地单机 | 个人试用、隐私优先用户 |
| **Pro** | 云端额度 + 文档AI + 调度 + 手机远控 | 本地 + 云端混合 | 进阶个人 / 小团队 |
| **Team-Self-hosted** | 审计导出 + 策略管控 + 私有部署 | 团队私有化部署 | 企业 / 合规团队 |

## 2. 能力矩阵

| 能力 (Feature) | Free | Pro | Team |
| --- | :---: | :---: | :---: |
| 本机只读 (`local_read_only`) | ✅ | ✅ | ✅ |
| 基础任务 (`basic_tasks`) | ✅ | ✅ | ✅ |
| 云端额度 (`cloud_quota`) | — | ✅ | ✅ |
| 文档 AI (`document_ai`) | — | ✅ | ✅ |
| 任务调度 (`scheduling`) | — | ✅ | ✅ |
| 手机远程查看 (`remote_view`) | — | ✅ | ✅ |
| 手机远程控制 (`remote_control`，高风险) | — | ✅ | ✅ |
| 审计导出 (`audit_export`) | — | — | ✅ |
| 策略管控 (`policy_management`) | — | — | ✅ |
| 私有部署 (`private_deployment`) | — | — | ✅ |

套餐为递进包含关系：**Free ⊂ Pro ⊂ Team**，高档自动继承低档全部能力。

## 3. 高风险能力：手机远控

手机远程控制（`remote_control`）是高风险能力，遵循「**付费层准入 + 逐次强审批**」双重约束：

1. **付费层准入（本次实现）**：仅 `pro` / `team` 套餐可用。`free` 套餐即使将 `LENGRVIS_REMOTE_DESKTOP_ENABLED` 设为 `true`，也会在设置闸门 `get_effective_settings()` 处被强制改回 `remote_desktop_enabled=False`，从而使所有下游远控守卫（WebSocket 鉴权、输入处理、会话保活）自动失效。
2. **逐次强审批（已有，不变）**：在已准入的付费套餐下，每一次远程输入仍按 R3 风险等级触发 `requires_approval=True`，经策略引擎复核、dry-run 预览，并创建移动端审批（`required_mobile_scopes` 含远程输入作用域）后方可执行。

> 准入门控不替代审批流：套餐解锁的是「可用性」，而非「免审批」。

## 4. 配置方式

```bash
# .env
LENGRVIS_PLAN=free   # free | pro | team
```

- 套餐属于部署 / 授权属性，来源以环境变量 `LENGRVIS_PLAN` 为准。
- 取值大小写不敏感，并接受常见别名（如 `professional`→pro、`team-self-hosted` / `enterprise`→team）；无法识别时回退到 `free`。
- 在代码中通过 `app.commerce.entitlements` 判定：`has_feature(plan, feature)` 做布尔判定，`require_feature(plan, feature)` 在未准入时抛出 `EntitlementError`（HTTP 402）。
- 桌面“设置 → 套餐与授权”会显示当前套餐、已启用能力、云端额度、授权主体和到期时间。
- 官方签发的 Ed25519 离线许可证可从该入口导入；导入前会验签和校验有效期，成功后原子写入本机数据目录。组织通过环境变量管理的许可证不能被桌面覆盖。

## 5. 后续路线 (Roadmap)

以下为本次未实现、建议后续迭代的能力：

- 套餐与许可证状态 API、桌面可见入口和离线 Ed25519 导入已落地。
- 云端用量计量与可选强制限流已落地；正式套餐额度仍需与真实托管成本和支付账单对齐。
- 在线激活、设备迁移、吊销同步、退款后自动降级和完整订阅生命周期仍待实现。
- 支付、税务、发票、订单、续费通知和客服工单尚未接入。
- 审计导出与策略管控（Team 档）的具体落地。
