# 法务与合规文档索引（Legal & Compliance Index）

> 状态口径：**fail-closed**。本目录记录真实状态，不写成"已合规"。未经执业律师定稿的文件一律标注为草稿，不得在对外发布中据此宣称合规。
> 最后更新：2026-06-24。

## 1. 文档清单与责任矩阵

| 文档 | 路径 | 适用对象 | 状态 | 法务定稿 |
| --- | --- | --- | --- | --- |
| 软件源码许可证 | `LICENSE` | 源码使用者 | BUSL-1.1 参数已落盘 | ⚠️ 商业发布前复核 |
| 隐私政策 | `docs/legal/privacy-policy.md` | 全体用户 | 草稿 v1.1 | ❌ 未完成 |
| 最终用户许可协议（EULA） | `docs/legal/eula.md` | 全体用户 | 草稿 v1.0 | ❌ 未完成 |
| 数据处理协议（DPA） | `docs/legal/data-processing-addendum.md` | 企业/Team 客户 | 草稿 v0.1 | ❌ 未完成 |
| 服务等级协议（SLA） | `docs/legal/sla.md` | 付费云端组件用户 | 草稿 v0.1（生效条件未满足） | ❌ 未完成 |
| 退款政策 | `docs/legal/refund-policy.md` | 付费用户 | 草稿 v0.1（支付未上线） | ❌ 未完成 |
| 子处理方清单 | `docs/legal/subprocessors.md` | 全体用户 | 草稿 v0.1 | ⚠️ 随接入更新 |
| 安全政策 / VDP | `SECURITY.md` + `.well-known/security.txt` | 安全研究者 | 生效 | n/a |
| 安全白皮书 | `docs/compliance/security-whitepaper.md` | 评估方/采购方 | 草稿 v0.1 | n/a |
| PIPL/GDPR 自查清单 | `docs/compliance/pipl-gdpr-checklist.md` | 内部 | 维护中 | n/a |
| 认证路线（SOC 2 / ISO 27001） | `docs/compliance/certification-roadmap.md` | 内部/采购方 | 规划 | n/a |

**运营主体**：cow milk（个人开发者，后续注册公司将自动继承）  
**联系邮箱**：mcow04717@gmail.com  
**适用法域**：中华人民共和国（PIPL）、欧盟（GDPR）、美国加州（CCPA/CPRA）

## 2. 法务发布门禁（Release Gate）

对外发布（公开分发渠道、官网、支付、账户体系任一上线）前，以下项必须全部满足，否则发布阻断：

- [ ] 隐私政策与 EULA 经执业律师定稿。
- [x] 桌面首启流程集成 EULA/隐私政策独立勾选与同意记录（含版本与时间戳）。
- [ ] 安装器许可展示与发布包法律资源完整性完成发布验证。
- [ ] 桌面 UI 提供数据删除入口（当前仅 API，见 `pipl-gdpr-checklist.md`）。
- [ ] 完整用户数据导出/导入（清单 #36）。
- [ ] 若上线支付：退款政策、SLA、子处理方清单随支付/云端服务同步定稿并公示。
- [ ] 若引入云端遥测/账户：子处理方清单更新并完成 PIA/DPIA。
- [ ] 个人信息保护影响评估（PIA/DPIA）完成。

> **免责声明**：本目录及其引用的法务文件由 AI 根据项目架构与代码审计报告生成，作为起点。正式发布前须由执业律师审阅，尤其针对 BSL 许可证效力、PIPL/GDPR/CCPA 合规细节及跨境传输条款。
