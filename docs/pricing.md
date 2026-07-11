# 定价与套餐设计 (Free / Plus / Pro)

> 本文档定义 Lengrvis 的商业化套餐分层、各档能力边界，以及高风险能力与付费层 + 强审批的绑定关系。
> 套餐通过环境变量 `LENGRVIS_PLAN`（`free` / `plus` / `pro`）选择，后端以 feature gating 落地（见 `backend/app/commerce/entitlements.py`）。旧值 `max` / `team` / `team-self-hosted` / `enterprise` 仅作为兼容别名，运行时会归一化为 `pro`。
> 状态：内部产品与技术基线，不构成公开报价或购买要约。价格、税务、支付渠道和合同条款在 `docs/business/market-readiness.md` 清零前不得对外承诺。

## 1. 套餐总览

| 套餐 | 定位 | 部署形态 | 目标用户 |
| --- | --- | --- | --- |
| **Free** | 有限官方额度的手动任务 | 本地单机 | 体验核心流程的个人用户 |
| **Plus · ¥49/月** | 正式自动化与跨网能力 | 本地 + 云端混合 | 高频运营 / 行政用户 |
| **Pro · ¥129/月** | Plus 全部能力 + 更强模型 + 更高额度 | 本地 + 云端混合 | 高强度任务与专业用户 |

## 2. 能力矩阵

| 能力 (Feature) | Free | Plus | Pro |
| --- | :---: | :---: | :---: |
| 本机只读 (`local_read_only`) | ✅ | ✅ | ✅ |
| 基础任务 (`basic_tasks`) | ✅ | ✅ | ✅ |
| 云端额度 (`cloud_quota`) | 有限 | ✅ | 更高 |
| 文档 AI (`document_ai`) | — | ✅ | ✅ |
| 任务调度 (`scheduling`) | — | ✅ | ✅ |
| 手机跨网查看 (`remote_view`) | — | ✅ | ✅ |
| 手机远程控制 (`remote_control`，高风险) | — | ✅ | ✅ |
| 审计导出 (`audit_export`) | — | ✅ | ✅ |
| 策略管控 (`policy_management`) | — | ✅ | ✅ |
| 私有部署 (`private_deployment`) | — | ✅ | ✅ |
| 更强模型 (`advanced_models`) | — | — | ✅ |

套餐为递进包含关系：**Free ⊂ Plus ⊂ Pro**。Plus 与 Pro 使用完全相同的审批、预算、隔离、凭据和移动安全控制；Pro 只增加模型质量与额度，不降低任何安全门槛。

## 3. 云端额度与成本护栏

云端 LLM 调用共用 `app.llm.usage` 用量账本，并由 `backend/app/commerce/usage.py` 默认强制限流。强制模式下如果用量账本不可读，会拒绝新的云调用，直到用量可核验；`LENGRVIS_CLOUD_QUOTA_ENFORCED=false` 仅用于本地开发临时关闭，不是商业发行默认值。

| 套餐 | Token 限额 |
| --- | --- |
| **Free** | 滚动 5 小时 500 万 tokens；滚动 7 天 2,000 万 tokens |
| **Plus** | 滚动 24 小时 1,000 万 tokens |
| **Pro** | 滚动 24 小时 1 亿 tokens |

`GET /api/commerce/usage/quota` 返回每个窗口的 `limits`、`usage`、`exceeded` 和总览字段；桌面“设置 → 套餐与授权”显示这些窗口，避免 Plus / Pro 被误读为无限。

## 4. 高风险能力：手机远控

手机远程控制（`remote_control`）是高风险能力，遵循「**付费层准入 + 逐次强审批**」双重约束：

1. **付费层准入（本次实现）**：仅 `plus` / `pro` 套餐可用。`free` 套餐即使将 `LENGRVIS_REMOTE_DESKTOP_ENABLED` 设为 `true`，也会在设置闸门 `get_effective_settings()` 处被强制改回 `remote_desktop_enabled=False`，从而使所有下游远控守卫（WebSocket 鉴权、输入处理、会话保活）自动失效。
2. **逐次强审批（已有，不变）**：在已准入的付费套餐下，每一次远程输入仍按 R3 风险等级触发 `requires_approval=True`，经策略引擎复核、dry-run 预览，并创建移动端审批（`required_mobile_scopes` 含远程输入作用域）后方可执行。

> 准入门控不替代审批流：套餐解锁的是「可用性」，而非「免审批」。

## 5. 配置方式

```bash
# .env
LENGRVIS_PLAN=free   # free | plus | pro
```

- 套餐属于部署 / 授权属性，来源以环境变量 `LENGRVIS_PLAN` 为准。
- 正式付费发行必须设置 `LENGRVIS_COMMERCIAL_RELEASE=true`；此时 `LENGRVIS_PLAN` 仅是配置提示，不能绕过许可证直接解锁 Plus/Pro，缺失、过期、吊销或验签失败都会回退 Free。
- 取值大小写不敏感，并接受旧别名（如 `max` / `team-self-hosted` / `enterprise`→pro）；无法识别时回退到 `free`。
- 新签发许可证携带 `plan_catalog=free-plus-pro-v1`；旧 Free/Pro/Max 许可证仍按旧含义解析为 Free/Plus/Pro，避免旧 Pro 被静默升级。
- 在代码中通过 `app.commerce.entitlements` 判定：`has_feature(plan, feature)` 做布尔判定，`require_feature(plan, feature)` 在未准入时抛出 `EntitlementError`（HTTP 402）。
- 桌面“设置 → 套餐与授权”会显示当前套餐、已启用能力、云端额度、授权主体和到期时间。
- 官方签发的 Ed25519 离线许可证可从该入口导入；导入前会验签和校验有效期，成功后原子写入本机数据目录。组织通过环境变量管理的许可证不能被桌面覆盖。
- 新签发许可证包含稳定 `license_id`；退款、拒付、换发或管理员撤销通过签名吊销清单生效。离线吊销需要客户或管理员部署更新后的清单，不是实时在线吊销。

## 6. 后续路线 (Roadmap)

以下为本次未实现、建议后续迭代的能力：

- 套餐与许可证状态 API、桌面可见入口和离线 Ed25519 导入已落地。
- 云端用量计量与默认强制 token 限流已落地；公开价格、税务、支付账单和超额处理仍需商业证据签收。
- 离线签发、换发关联、签名吊销清单、退款后本机降级和首次在线订阅 key 激活已落地；公开自助 checkout、自动吊销同步和完整订阅生命周期仍待生产证据签收。
- 支付、税务、发票、订单、续费通知和客服工单尚未接入。
- 首发支付渠道仅为支付宝；在支付、税务、退款、法务和支持证据清零前，不得把固定价格描述为已可公开购买。
