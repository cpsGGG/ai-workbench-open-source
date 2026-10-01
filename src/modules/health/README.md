# 健康模块前端契约

入口：`createHealthWorkspace(root)`。模块自行渲染、绑定事件并从本机共享 API 水合数据；无演示数据。

## 数据与 API

权威数据源为 `GET /api/health-storage` 和 `PUT /api/health-storage`。

GET 成功响应：

```json
{
  "ok": true,
  "initialized": true,
  "profile": {},
  "entries": [],
  "revision": 3,
  "updatedAt": "2026-08-11T12:00:00.000Z"
}
```

PUT 请求：

```json
{
  "profile": {},
  "entries": [],
  "baseRevision": 3
}
```

PUT 成功响应与 GET 相同。revision 冲突返回 HTTP 409：

```json
{
  "ok": false,
  "error": "revision_conflict",
  "state": {
    "profile": {},
    "entries": [],
    "revision": 4
  }
}
```

前端会按日期去重每日记录，以 `updatedAt` / `deletedAt` 最新者为准合并，再使用新 revision 自动重试，最多 3 次。`deletedAt` 墓碑会保留在 API 数据中，避免旧缓存让删除记录复活。

`localStorage` 只保存共享数据备份、一次性旧数据迁移标记和当前选择日期：

- `ai-workbench:health:v1`
- `ai-workbench:health:shared-store-migrated:v1`
- `ai-workbench:health:selected-date:v1`

服务离线时只显示备份并禁用全部数据表单，不会把浏览器缓存冒充为成功保存。只有旧备份确实含有 profile、记录或墓碑时，首次水合才会将其与远端合并；空缓存不会初始化或覆盖服务端数据。

### profile

- `focus`: `recomposition | muscle_gain | fat_loss | boxing | general`
- `currentWeightKg`, `targetWeightKg`, `targetBodyFatPct`
- `dailyProteinG`, `dailyWaterMl`, `sleepGoalHours`
- `weeklyStrengthGoal`, `weeklyBoxingGoal`
- `note`, `updatedAt`

### entries[]

每个日期最多一条，稳定键为 `date`，默认 id 为 `health-day:YYYY-MM-DD`。

- 身体：`weightKg`, `waistCm`, `energyLevel`
- 训练：`training.strengthDone/Minutes`, `boxingDone/Minutes`, `recoveryDone/Minutes`, `note`
- 饮食：`nutrition.proteinG`, `waterMl`, `vegetableServings`, `healthyMeals`, `note`
- 睡眠：`sleep.durationHours`, `quality`, `bedtime`, `wakeTime`
- 通用：`note`, `createdAt`, `updatedAt`, `deletedAt`

## 样式 hooks

布局与状态：

- `healthTopbar`, `healthDateNav`, `healthStatusBar`, `healthWorkspace`
- `healthNotice saving|success|error`
- `healthMainGrid`, `healthDailyPanel`, `healthGoalPanel`, `healthTrendPanel`

概览：

- `healthOverviewSection`, `healthOverviewGrid`
- `healthOverviewCard weight|training|nutrition|sleep`
- `healthHabitStrip`, `healthHabitChip done`, `healthCompletionBadge`

表单：

- `healthSectionHeader`, `healthSectionKicker`
- `healthDailyForm`, `healthGoalForm`, `healthFormSection`, `healthFormSectionTitle`, `healthFormIcon`
- `healthFieldGrid compact`, `healthField`, `healthWideField`, `healthInputWithUnit`
- `healthTrainingGrid`, `healthTrainingCard done`, `healthTrainingCheck`, `healthCheckMark`, `healthTrainingMinutes`
- `healthWaterQuick`, `healthQuickButton`, `healthFormActions`
- `healthGoalWeightGrid`, `healthGoalDivider`, `healthGoalSubmit`

趋势与历史：

- `healthTrendSummary`, `healthTrendMetric`, `healthTrendStrip`
- `healthTrendDay selected|hasData`, `healthTrendBar`
- `healthHistoryList`, `healthHistoryHeader`, `healthHistoryRow hasData`

移动端建议在 `healthMainGrid`、`healthOverviewGrid`、`healthFieldGrid` 和 `healthHistoryRow` 上切成单列或横向滚动；触控按钮最小高度建议 40px。
