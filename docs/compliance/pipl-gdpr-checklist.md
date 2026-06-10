# PIPL / GDPR 合规自查清单（市场化清单 #14）

> 状态口径：fail-closed。本文档记录当前真实合规状态，不写成"已合规"。
> 最后更新：2026-06-10。法务复核：**未完成**（阻断项，见底部）。

## 1. 数据处理概览（ROPA 简版）

| 数据类别 | 存储位置 | 处理目的 | 默认是否离开本机 |
| --- | --- | --- | --- |
| 任务目标/对话正文（tasks、chat_messages、runs、agent_messages） | 本机 SQLite（`<data_dir>/lengrvis.db`） | 执行用户委托的任务 | 否；efficiency/hybrid 模式下任务文本会发送到用户配置的云端 LLM provider |
| 任务录屏/步骤截图（task_recordings） | 本机 SQLite BLOB | opt-in 证据回放（默认关闭，`LENGRVIS_TASK_RECORDING_ENABLED` 未设置时不采集） | 否 |
| 文件索引/文档分块与向量（indexed_files、document_chunks、document_chunk_embeddings） | 本机 SQLite | 本地文件搜索、文档问答 | 否 |
| 移动配对/设备/远控授权（mobile_pairings、mobile_devices、grants） | 本机 SQLite | 手机伴侣配对与短期远控授权 | 否（LAN 内传输，非 loopback 要求 WSS） |
| 审批/安全审查记录（approvals、safety_reviews） | 本机 SQLite | 危险操作审批与审计 | 否 |
| 审计链（audit_events，HMAC 防篡改） | 本机 SQLite | 安全审计、可追责 | 否 |
| 诊断包 | `<data_dir>/diagnostic-packages/*.json` | 用户主动导出排障；导出时执行脱敏（路径标签化、敏感字段移除），`public_safe=false` | 仅当用户手动外发 |
| 日志 | `logs/`、`<data_dir>/logs/` | 本机排障；写入前经脱敏中间件 | 否 |
| 云端遥测 | 无 | 当前无任何云端 telemetry/账户系统 | 不适用 |

## 2. 删除权（PIPL 第47条 / GDPR Art.17）

- [x] **本机一键删除入口**：`POST /api/system/privacy/erase-local-data`（需显式确认词 `erase-local-data`）。
  - 删除范围：任务、对话、运行记录、录屏、审批、配对设备、记忆、文件索引、LLM 用量事件、感知观察、已导出诊断包。
  - 默认保留：`app_settings`/`permission_policies`（可用 `include_settings=true` 一并删除）；审计链保留并追加 `privacy.local_data_erased` 事件（安全留痕的合法利益基础），数据库执行 VACUUM 防止已删行残留在空闲页。
  - 证据：`backend/tests/test_privacy_erase.py`（3 passed，2026-06-10）。
- [ ] **桌面 UI 删除入口**：设置页尚未提供该端点的按钮（当前仅 API）。
- [ ] **日志自动清理**：日志目录当前为手动清理路径（设置→系统信息可见位置）。
- [x] **无云端账户数据**：当前产品无账户体系/云端存储，无需云端删除流程；若未来引入需补。

## 3. 其余义务自查

| 义务 | 状态 | 说明 |
| --- | --- | --- |
| 告知同意（隐私政策展示与同意记录） | ❌ 未完成 | 隐私政策仅有草稿（`docs/legal/privacy-policy-draft.md`），安装器未集成同意勾选 |
| 最小必要收集 | ✅ 设计满足 | 本地处理为主；无遥测；录屏 opt-in 默认关闭 |
| 数据可携权（导出） | ⚠️ 部分 | 诊断包可导出但非完整用户数据导出；完整导出/导入见清单 #36（未完成） |
| 跨境传输 | ⚠️ 取决于用户配置 | efficiency/hybrid 模式下任务文本发往用户自配 LLM provider；隐私模式不出本机且不静默回退（有测试证据）。隐私政策定稿需明示 |
| 安全保障措施 | ⚠️ 部分 | 审计链 HMAC、脱敏中间件、审批边界有测试证据；第三方渗透测试未做（清单 #4） |
| 未成年人保护 | ❌ 未评估 | 法务定稿时一并评估 |
| 个人信息保护影响评估（PIA） | ❌ 未做 | 建议随首个对外版本完成 |

## 4. 阻断项（不得在对外发布中宣称合规）

1. 隐私政策与 EULA 法务定稿 + 安装时展示并同意（清单 #5）。
2. 桌面 UI 的删除入口与文案。
3. 完整用户数据导出/导入（清单 #36）。
4. 若上线官网/支付/账户体系，本清单需全部重审。
