# 商业法务签收清单

状态：fail-closed 草案。本清单是内部证据收集契约，不是法律意见，也不表示引用的法律文件已经获批。

付费/公开发布前，律师或已批准的 legal owner 必须为下列每一项提供红acted 批准标签。这些标签记录在 `commercial-operations-evidence-reviewed` 中。

## 必要签收领域

任何法律文件或公开付费 claims 被视为最终发布证据前，必须完成 counsel approval。

| 领域 | 闭环要求 |
| --- | --- |
| 签约主体 | 法律名称、销售授权、地址/公开披露处理、非个人法律联系渠道。 |
| 法规来源登记 | 按发布日期、目标市场和产品/收款模式复核 `docs/legal/legal-source-register.md`。 |
| 法律风险 memo | 复核 `docs/legal/commercial-legal-risk-memo.md`，关闭或豁免每个 `LGL-P0` 项，并附处置证据。 |
| EULA | 桌面软件最终客户条款、许可限制、终止、保证免责声明和责任限制。 |
| 隐私政策 | 最终数据类别、本地/云端处理、诊断处理、留存、删除、子处理方和联系路径。 |
| 退款政策 | 最终退款窗口、排除项、取消处理、拒付冲突处理、法定权利和吊销语言。 |
| DPA/SLA 适用性 | 何时提供 DPA/SLA、哪些客户适用、哪些云端/支持承诺被排除。 |
| 消费者撤回条款 | 各法域冷静期/撤回权条款，以及数字服务例外。 |
| 支持销售法域 | 已批准销售、税务、隐私和消费者条款的国家/地区。 |
| 公开 claims | 定价、合规、安全、隐私、支持和退款 claims 的法律审查。 |

## Fail-Closed 规则

1. 法律草案不授权 checkout。
2. 支付沙箱通过不代表 live tax 或消费者条款获批。
3. 支持 runbook 不会自动形成公开 SLA，除非法律条款明确批准。
4. Claims 必须针对精确公开表面审查，不能只审查释义或摘要。
5. 法务批准标签必须红acted；不得提交律师邮箱、客户数据、带签名合同 PDF 或私有事项链接。

## 证据路径

商业运营 JSON 应通过以下字段引用本清单：

- `legal.counsel_review_label`
- `legal.legal_source_register_label`
- `legal.legal_risk_memo_label`
- `legal.eula_final_label`
- `legal.privacy_policy_final_label`
- `legal.refund_policy_final_label`
- `legal.dpa_sla_applicability_label`
- `legal.consumer_withdrawal_terms_label`
- `legal.supported_jurisdictions_label`
- `legal.public_contact_terms_label`
