# 付费商业运营闭环

本 runbook 定义付费/公开发布运营门禁。它不替代法律顾问、税务意见、支付处理方账户或商业 owner 签收；它的作用是把这些外部批准变成明确、红acted、已审查、可机器校验的证据，然后才允许 Lengrvis 收款、开票、发布付费定价或称付费套餐已公开可用。

reviewed evidence 类型为 `commercial-operations-evidence-reviewed`，通过以下命令校验：

```powershell
npm run evidence:commercial-operations-verify
```

默认 reviewed evidence 路径：

```text
build/commercial-operations-evidence-reviewed.json
```

如需指定其他 reviewed JSON 文件，可设置 `LENGRVIS_COMMERCIAL_OPERATIONS_EVIDENCE_PATH`。

## 范围

证据范围必须是 `paid_public_launch`。无销售维护包不需要此 pass，但也不得收款、发布付费价格、开票或宣称商业可用。

## 必要闭环领域

| 领域 | 闭环要求 |
| --- | --- |
| 签约主体 | 已审查的签约实体、公开地址或豁免、账单描述符、非个人 legal/privacy/support 联系渠道。 |
| 税务 | 税务 owner、登记或豁免依据、法域矩阵、产品税务属性审查、发票税务显示、申报/会计 runbook，来源见 `docs/business/payment-tax-operations.md`。 |
| 收款 | 商户或人工开票账户、checkout/开票流程、收据样本、结算/对账 runbook、拒付流程，以及仓库不含卡号/银行/支付密钥的证据。 |
| 法务 | 已由法律顾问审查的 EULA、隐私政策、退款政策、DPA/SLA 适用性、消费者撤回条款、支持销售法域和公开联系方式条款，来源见 `docs/legal/commercial-legal-approval-checklist.md`。 |
| 客服 | 已审查的 support/privacy 证据、受监控渠道、owner 排班、严重级和 SLA 条款、隐私升级、诊断留存、客服脚本，来源见 `docs/business/support-refund-operations.md`。 |
| 退款 | 退款政策、入口、决策矩阵、许可证吊销路径、收据或 credit note 处理、拒付/退款冲突处理、对账日志，来源见 `docs/business/support-refund-operations.md`。 |
| 公开 claims | claims 证据、claims register、定价页、权益对齐、安全/隐私审查、禁止 claims、发布/回滚文案，来源见 `docs/business/public-claims-register.md`。 |

## 运营源文件

- `docs/business/payment-tax-operations.md`：收款、税务、对账和拒付流程。
- `docs/business/support-refund-operations.md`：客户账单分类、退款决策、客户跟进、退款到许可证吊销流程。
- `docs/business/public-claims-register.md`：允许/禁止的公开 claims 及审查记录。
- `docs/legal/commercial-legal-approval-checklist.md`：法务批准证据要求，且不把法律草案伪装成最终结论。
- `docs/business/support-privacy-operations.md`：隐私/支持演练要求。
- `docs/business/license-operations.md`：签发、激活、替换和吊销机制。

## 证据契约

reviewed JSON 只能包含红acted 标签，不能包含原始客户数据、邮箱、本地路径、主机名、支付详情、银行卡数据、API key、激活 key、配对码、token 或私有 URL。

校验器要求：

- `candidate.commit` 和 `candidate.build_identifier`
- `operations.scope = paid_public_launch`
- 上述每个领域的 `status = passed`
- reviewer 标签和 ISO 格式 `reviewed_at_utc`
- `summary.commercial_operations_ready = true`
- `summary.paid_public_launch_signoff = false`
- `summary.release_signoff = false`
- 使用 reviewer 独占的 `LENGRVIS_REVIEWED_EVIDENCE_PRIVATE_KEY` 生成 `reviewed-evidence-ed25519/v3` 签名；验证侧只配置 `LENGRVIS_REVIEWED_EVIDENCE_PUBLIC_KEY`，不得取得私钥

最后两个 summary sign-off 字段必须保持 false，因为此 artifact 是证据，不是发布授权。发布授权仍来自商业 owner 更新 `docs/business/market-readiness.md` 并运行：

```powershell
npm run delivery:paid-launch
```

## 与现有门禁的关系

这份运营证据是附加门禁：

- `commercial-loop-evidence-reviewed` 证明订阅激活、许可证签发、开票/退款/拒付演练和吊销行为。
- `support-privacy-operations-evidence-reviewed` 证明客服/隐私 owner 与演练。
- `claims-launch-evidence-reviewed` 证明付费发布素材和公开 claims。
- `commercial-operations-evidence-reviewed` 把法务、税务、收款、客服、退款和 claims 串成一个已审查的 owner 交接证据。

在付费/公开发布 market readiness 通过前，`delivery:paid-launch` 必须要求这四类证据全部存在。

## 模板

生成 fail-closed 模板：

```powershell
npm run evidence:paid-launch-template
```

生成的模板不是 reviewed evidence，也不得记录为 paid-launch pass。

## 封存 Reviewed Evidence

商业 owner 用真实法务、支付、税务、客服、退款和 claims 证据填完 reviewed JSON 后，用以下命令封存：

```powershell
npm run evidence:keypair -- `
  --private-key-output "$env:TEMP\lengrvis-reviewed-evidence-private.key" `
  --public-key-output "$env:TEMP\lengrvis-reviewed-evidence-public.key"

$env:LENGRVIS_REVIEWED_EVIDENCE_PRIVATE_KEY = Get-Content "$env:TEMP\lengrvis-reviewed-evidence-private.key" -Raw
$env:LENGRVIS_REVIEWED_EVIDENCE_PUBLIC_KEY = Get-Content "$env:TEMP\lengrvis-reviewed-evidence-public.key" -Raw
npm run evidence:commercial-operations-seal -- `
  --input path\to\commercial-operations-evidence.reviewed.draft.json `
  --output build\commercial-operations-evidence-reviewed.json
```

私钥文件必须导入 reviewer 独占的密钥存储，导入确认后删除临时文件；不能配置到 candidate、publish 或普通验证工作流。公钥内容配置为 GitHub Actions variable `LENGRVIS_REVIEWED_EVIDENCE_PUBLIC_KEY`。封存命令会拒绝模板、无效 Ed25519 密钥、错误 artifact type、缺失必填标签、原始敏感值，以及无法通过 `npm run evidence:commercial-operations-verify` 的证据。封存仍不是 release sign-off；它只创建 `delivery:paid-launch` 使用的已签名 reviewed evidence artifact。
