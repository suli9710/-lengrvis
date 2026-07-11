# 公开 Claims 登记表

状态：fail-closed 草案。本登记表控制 Lengrvis 在付费定价、发布素材、发布说明、onboarding、客服脚本和公开安全/隐私文案中可以说什么。

在 `claims-launch-evidence-reviewed`、`commercial-operations-evidence-reviewed` 和 `market:readiness:paid` 全部通过前，本文中的任何 claim 都不得视为已批准公开付费发布。

## 唯一事实来源

| Claim 领域 | 来源 |
| --- | --- |
| 套餐名称和能力矩阵 | `docs/pricing.md` |
| 运行时权益行为 | `backend/app/commerce/entitlements.py` |
| 用量额度强制 | `backend/app/commerce/usage.py` |
| 许可证签发与吊销 | `docs/business/license-operations.md` |
| 客服/隐私运营 | `docs/business/support-privacy-operations.md` |
| 收款/税务/退款运营 | `docs/business/payment-tax-operations.md`; `docs/business/support-refund-operations.md` |
| 法律条款 | `docs/legal/README.md` 及其链接的法律草案/最终文档 |

## 允许的内部 Claims

market readiness 仍为 waived 时，下列内容仅是内部产品/规划 claim：

| Claim | 公开使用前需要的证据 |
| --- | --- |
| Free、Plus（¥49/月）、Pro（¥129/月）是已锁定的商业套餐层级。 | 权益测试和 `docs/pricing.md` 审查；支付 GA 仍受市场与法务门禁阻断。 |
| Plus 和 Pro 可解锁正式自动化与跨网能力。 | 权益门控加逐次 IntentCapsule、预算与高风险审批证据。 |
| 云端额度通过用量窗口强制执行。 | 用量测试和成本 owner 对公开额度文案的批准。 |
| 离线许可证使用 Ed25519 签名验证。 | 生产公钥、签发方 custody 和吊销 manifest 证据。 |
| 退款可以撤销付费权益。 | 退款演练加签名吊销 manifest 部署证据。 |

## 禁止 Claims

不得发布或暗示：

- 付费套餐已公开可用；
- live checkout 或自助账单门户已上线；
- 在任何法域提供税务合规发票；
- 条款已由律师批准或已实现监管合规；
- SOC 2、ISO 27001、HIPAA、GDPR、PIPL、CCPA/CPRA 或同等合规；
- 硬件证明或离线许可证实时吊销；
- 无限用量、无限席位或无限支持；
- 保证 SLA 或响应时间；
- 零数据收集、零风险或完全隐私；
- 无需人工或支付处理方审查的自动退款。

## 审查记录

每条公开 claim 必须记录：

- claim ID；
- 将要发布的精确文案；
- 出现表面；
- 来源文件或素材；
- 支撑证据标签；
- reviewer 标签；
- 审查日期；
- 处置结论：approved、limited、blocked 或 removed。

`commercial-operations-evidence-reviewed` payload 应通过 `public_claims.claims_register_label` 引用已批准登记表。
