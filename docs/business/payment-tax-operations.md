# 收款与税务运营手册

状态：fail-closed 草案。本文定义 Lengrvis 在任何付费/公开发布前必须收集的证据。它不是税务意见、法律意见、支付处理方批准或商业 sign-off。

## 发布规则

在 `commercial-operations-evidence-reviewed` 通过 `npm run evidence:commercial-operations-verify`，且 `docs/business/market-readiness.md` 中每个 `MR-P0` 行都是 `passed` 前，不得收款、开票、启用 checkout、发布付费定价或宣称付费套餐已公开可用。

## 收款

发布前，商业 owner 必须附上以下红acted 证据标签：

- 收款模型：托管 checkout、托管账单门户、merchant of record 或人工开票；
- 已批准的支付处理方/账户或人工开票系统；
- checkout 或开票流程，包括取消入口；
- 已审查税务显示的收据或发票样本；
- 结算、打款和对账 runbook；
- webhook 处理或人工结算 fallback；
- 拒付受理、证据和截止日期流程；
- 仓库/CI 扫描，证明未提交卡号、银行信息、支付处理方密钥或 webhook 签名密钥。

优先使用托管账单门户处理支付方式更新、发票访问、取消和订阅变更。除非支付处理方门户无法满足发布政策，否则自研账单 UI 应作为后续产品表面，而不是当前闭环前提。

## 税务

发布前，税务 owner 必须附上以下红acted 证据标签：

- 税务 owner 和备份 owner；
- 税务登记、merchant-of-record 覆盖或书面豁免依据；
- 支持销售法域矩阵；
- 订阅、许可证以及捆绑专业服务组件的产品税务属性审查；
- 发票/收据税务显示审查；
- 申报/会计 runbook 和月结 owner；
- 免税客户或税务争议的客服升级路径。

税务沙箱计算只能作为演练证据，不能作为 live tax pass。

## 对账

每个付费周期必须对齐四本账：

| 账本 | 必要键 | 闭环证据 |
| --- | --- | --- |
| 支付处理方或开票系统 | payment、invoice 或 credit-note 引用 | 已结算金额、手续费、税额和打款状态。 |
| 订阅/激活系统 | subscription ID 或 activation-key 引用 | 套餐、期限、状态和客户可见权益。 |
| 许可证签发账本 | `license_id` 和红acted `order_ref` | 签发、替换、吊销和 manifest 发布。 |
| 客服/退款 case log | case ID | 客户请求、决策、动作和跟进。 |

对账记录必须使用不透明引用，不得使用卡号、原始邮箱、银行信息、许可证 token、激活 key 或私有 URL。

## 拒付

收到拒付通知后：

1. 创建账单 case，并冻结同一笔 charge 的重复退款处理。
2. 分类争议：重复扣费、欺诈、产品失败、取消争议、税务/发票问题或未知。
3. 附上红acted 证据：收据/发票、权益交付、许可证激活/吊销状态、支持历史和政策条款。
4. 只有在审查支付处理方时限和重复退款风险后，才能决定申诉、接受或单独退款。
5. 如果 charge 败诉或退款，触发 `docs/business/license-operations.md` 中的许可证吊销流程，并记录 manifest 发布。

## 不得记录支付秘密

仓库、CI 日志、发布证据、诊断包和客服 case 不得包含卡号、银行信息、支付处理方 API key、webhook 签名密钥、支付方式指纹或原始托管 checkout session URL。证据必须使用红acted 标签或不透明内部引用。

## 证据标签

商业运营 JSON 应通过以下字段引用本 runbook：

- `payment_collection.collection_model_label`
- `payment_collection.reconciliation_runbook_label`
- `payment_collection.chargeback_runbook_label`
- `payment_collection.no_card_or_bank_secrets_in_repo_label`
- `tax.tax_jurisdiction_matrix_label`
- `tax.product_taxability_review_label`
- `tax.invoice_tax_display_label`
- `tax.remittance_accounting_runbook_label`
