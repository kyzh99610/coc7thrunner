# PHASE2_BASELINE_SUMMARY

## 当前结论
本地 Phase 2 稳定基线已建立完成，当前仓库已具备以下能力：

- desktop 最小 smoke 可跑通
- 完整 3-beat smoke regression 已自动化
- keeper prompt 默认 assignee 会绑定当前 session keeper
- keeper export 端点可导出 keeper 视角会话
- session raw snapshot 可导出
- session 可从 snapshot import 恢复为新 session
- snapshot/import 已完成一次人工 smoke 验证
- 历史 keeper assignee 漂移（如 `keeper_wow_001`）已做兼容显示，不改 raw snapshot

## 当前推荐 tag
- `desktop-smoke-baseline`
- `full-smoke-regression`
- `keeper-assignee-fix`
- `keeper-session-export`
- `rules-grounding-negative-regression`
- `session-snapshot-import`
- `snapshot-import-smoke-validated`
- `legacy-keeper-assignee-compat`
- `phase2-stable-baseline`

## 当前人工验证结论
已验证以下本地流程可用：

1. 中途 session 可通过 `GET /sessions/{id}/snapshot` 导出 raw SessionState
2. snapshot JSON 可保存到 `notes/session-dumps/`
3. 可通过 `POST /sessions/import` 从 snapshot 恢复出新 session
4. 新 session 可继续 `GET /state`
5. 新 session 可继续提交 `player-action`
6. 新 session 可继续提交 `manual-action`

## 当前已知的非阻塞问题
- 历史 prompt 的 raw `assigned_to` 仍可能保留旧值，如 `keeper_wow_001`
- keeper 视图和 keeper prompt 更新响应已做兼容显示
- raw snapshot 不做历史数据重写
- 跨环境 import 时，不额外校验 knowledge source 是否存在

## 当前推荐手工流程
### 存档
- `GET /sessions/{id}/snapshot`
- 保存为 `notes/session-dumps/<session-id>-snapshot.json`

### 读档
- `POST /sessions/import`
- 记录返回的 `new_session_id`
- 对新 session 调 `GET /sessions/{new_id}/state?viewer_role=keeper`
- 继续提交 `player-action` / `manual-action`

## 下一阶段最值得做的方向
- prompt lifecycle 的 auto-dismiss / expires-after-beat
- session diff / checkpoint tooling
- rules grounding 更多真实 query 样本覆盖
- scenario authoring 体验增强