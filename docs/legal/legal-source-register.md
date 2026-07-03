# 付费/公开发布法规来源登记表

状态：fail-closed 法务研究登记表。  
最后核验：2026-07-03。

本登记表记录 Lengrvis 付费或公开发布前必须复核的官方法规来源。本文不是法律意见，也不能替代目标法域执业律师的正式审查。

## 官方来源

| 主题 | 官方来源 | 发布相关性 |
| --- | --- | --- |
| 中国个人信息保护 | 全国人民代表大会：`https://www.npc.gov.cn/WZWSREL25wYy9jMi9jMzA4MzQvMjAyMTA4L3QyMDIxMDgyMF8zMTMwODguaHRtbD9yZWY9NzEy` | 隐私政策、个人信息主体权利、删除、单独同意、跨境处理、PIA/DPIA 证据。 |
| 欧盟 GDPR | EUR-Lex Regulation (EU) 2016/679：`https://eur-lex.europa.eu/eli/reg/2016/679/oj/eng` | 透明告知、控制者/处理者角色、DPA 条款、数据主体权利、安全、泄露通知、传输机制。 |
| 加州 CCPA/CPRA | CPPA 2026-01-01 生效文本：`https://cppa.ca.gov/regulations/pdf/ccpa_statute_eff_20260101.pdf` | 收集前告知、隐私政策内容、消费者权利、出售/共享/敏感数据披露、必要且相称的数据收集。 |
| 美国订阅/自动续费取消 | FTC Negative Option Rule hub：`https://www.ftc.gov/legal-library/browse/rules/negative-option-rule` | 付费订阅文案、续费同意、取消路径、账单门户设计，以及取消不得比注册更困难的证据。 |
| 美国 click-to-cancel 商业指引 | FTC Business Blog：`https://www.ftc.gov/business-guidance/blog/2024/10/click-cancel-ftcs-amended-negative-option-rule-what-it-means-your-business` | 订阅注册/取消 UX 的运营清单；面向美国销售前需持续监控规则与执法变化。 |
| 中国消费者保护实施规则 | 国务院条例：`https://big5.www.gov.cn/gate/big5/www.gov.cn/zhengce/content/202403/content_6940158.htm` | 面向中国消费者销售、个人信息处理、网络交易表示、退款/取消文案、自动续费审查。 |
| 加州隐私告知实务说明 | CPPA notices PDF：`https://cppa.ca.gov/pdf/general_notices.pdf` | 加州相关页面的收集前告知与隐私政策放置清单。 |

## 证据要求

`MR-P0-002` 通过前，商业运营证据必须包含：

- `legal.legal_source_register_label`：红acted 标签，说明针对发布日期和目标法域复核了哪些法规来源版本；
- `legal.legal_risk_memo_label`：红acted 标签，指向法律风险 memo 及处置结论；
- `legal.supported_jurisdictions_label`：已批准销售的法域；
- `public_claims.prohibited_claims_label`：因缺少法律依据或证明材料而移除/阻断的公开 claims。

## 监控规则

1. 变更目标市场、收款模式、订阅续费行为、遥测、支持承诺或公开合规 claims 前，必须重新复核本登记表来源。
2. 如果目标市场包含本表未列出的法域，必须先补充官方来源，再批准发布文案。
3. 如果官方来源发生变化，必须重新生成 reviewed evidence，不得复用旧的法务批准标签。
