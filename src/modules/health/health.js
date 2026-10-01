const HEALTH_STORAGE_ENDPOINT = "/api/health-storage";
const HEALTH_BACKUP_KEY = "ai-workbench:health:v1";
const HEALTH_MIGRATED_KEY = "ai-workbench:health:shared-store-migrated:v1";
const HEALTH_SELECTED_DATE_KEY = "ai-workbench:health:selected-date:v1";

const FOCUS_OPTIONS = [
  ["recomposition", "增肌减脂并行"],
  ["muscle_gain", "增肌优先"],
  ["fat_loss", "减脂优先"],
  ["boxing", "拳击表现优先"],
  ["general", "综合健康"],
];

export function createHealthWorkspace(root) {
  const backup = loadHealthBackup();
  let snapshot = backup.snapshot;
  let selectedDate = loadSelectedDate();
  let revision = 0;
  let storageReady = false;
  let storageLoading = true;
  let saving = false;
  let notice = null;
  let migrated = loadMigrationMarker();

  function selectedEntry() {
    return activeEntries(snapshot.entries).find((entry) => entry.date === selectedDate) || emptyEntry(selectedDate);
  }

  async function syncFromSharedStorage() {
    storageLoading = true;
    render();
    try {
      let state = await fetchHealthStorage();
      revision = state.revision;
      let nextSnapshot = normalizeSnapshot(state);
      let mergedDuringMigration = false;

      if (!migrated && backup.exists && hasHealthData(backup.snapshot)) {
        nextSnapshot = mergeSnapshots(backup.snapshot, nextSnapshot);
        const result = await persistWithConflictRetry(nextSnapshot, revision);
        state = result.state;
        nextSnapshot = normalizeSnapshot(state);
        revision = state.revision;
        mergedDuringMigration = result.merged;
      }

      snapshot = nextSnapshot;
      storageReady = true;
      migrated = true;
      saveMigrationMarker();
      saveHealthBackup(snapshot);
      notice = mergedDuringMigration
        ? { state: "success", message: "已合并此浏览器的旧健康记录与共享数据。" }
        : null;
    } catch (error) {
      storageReady = false;
      notice = {
        state: "error",
        message: healthStorageErrorMessage(error, "本机健康数据服务暂时不可用。已显示浏览器备份，恢复服务后刷新即可继续编辑。"),
      };
    } finally {
      storageLoading = false;
      render();
    }
  }

  async function persistWithConflictRetry(desiredSnapshot, baseRevision) {
    let candidate = normalizeSnapshot(desiredSnapshot);
    let candidateRevision = baseRevision;
    let merged = false;
    let latestConflictState = null;

    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        const state = await writeHealthStorage(candidate, candidateRevision);
        return { state, merged };
      } catch (error) {
        if (error?.code !== "revision_conflict" || !error.state) throw error;
        latestConflictState = error.state;
        candidateRevision = normalizeRevision(error.state.revision);
        candidate = mergeSnapshots(candidate, normalizeSnapshot(error.state));
        merged = true;
      }
    }

    const error = new Error("revision_conflict");
    error.code = "revision_conflict";
    error.state = latestConflictState;
    throw error;
  }

  async function commitSnapshot(nextSnapshot, successMessage) {
    if (saving || !storageReady) return false;
    saving = true;
    notice = { state: "saving", message: "正在保存到本机共享健康数据…" };
    render();

    try {
      const result = await persistWithConflictRetry(nextSnapshot, revision);
      snapshot = normalizeSnapshot(result.state);
      revision = normalizeRevision(result.state.revision);
      saveHealthBackup(snapshot);
      notice = {
        state: "success",
        message: result.merged ? `已合并其他页面的更新并${successMessage}` : successMessage,
      };
      return true;
    } catch (error) {
      if (error?.code === "revision_conflict" && error.state) {
        snapshot = normalizeSnapshot(error.state);
        revision = normalizeRevision(error.state.revision);
        saveHealthBackup(snapshot);
      }
      notice = {
        state: "error",
        message: healthStorageErrorMessage(error, "健康数据保存失败，本次修改没有覆盖原数据。请稍后重试。"),
      };
      return false;
    } finally {
      saving = false;
      render();
    }
  }

  async function saveDailyEntry(form) {
    const data = new FormData(form);
    const previous = snapshot.entries.find((entry) => entry.date === selectedDate);
    const now = new Date().toISOString();
    const entry = normalizeEntry({
      id: previous?.id || `health-day:${selectedDate}`,
      date: selectedDate,
      weightKg: numberOrNull(data.get("weightKg"), 20, 400),
      waistCm: numberOrNull(data.get("waistCm"), 30, 250),
      energyLevel: integerOrNull(data.get("energyLevel"), 1, 5),
      training: {
        strengthDone: data.has("strengthDone"),
        strengthMinutes: numberOrNull(data.get("strengthMinutes"), 0, 600),
        boxingDone: data.has("boxingDone"),
        boxingMinutes: numberOrNull(data.get("boxingMinutes"), 0, 600),
        recoveryDone: data.has("recoveryDone"),
        recoveryMinutes: numberOrNull(data.get("recoveryMinutes"), 0, 600),
        note: cleanText(data.get("trainingNote"), 2000),
      },
      nutrition: {
        proteinG: numberOrNull(data.get("proteinG"), 0, 1000),
        waterMl: numberOrNull(data.get("waterMl"), 0, 15000),
        vegetableServings: numberOrNull(data.get("vegetableServings"), 0, 30),
        healthyMeals: integerOrNull(data.get("healthyMeals"), 0, 12),
        note: cleanText(data.get("nutritionNote"), 2000),
      },
      sleep: {
        durationHours: numberOrNull(data.get("sleepHours"), 0, 24),
        quality: integerOrNull(data.get("sleepQuality"), 1, 5),
        bedtime: normalizeTime(data.get("bedtime")),
        wakeTime: normalizeTime(data.get("wakeTime")),
      },
      note: cleanText(data.get("dailyNote"), 3000),
      createdAt: previous?.createdAt || now,
      updatedAt: now,
      deletedAt: "",
    });

    if (!entry) return;
    const entries = upsertEntry(snapshot.entries, entry);
    await commitSnapshot({ profile: snapshot.profile, entries }, `${formatShortDate(selectedDate)}的记录已保存。`);
  }

  async function saveGoals(form) {
    const data = new FormData(form);
    const profile = normalizeProfile({
      focus: data.get("focus"),
      currentWeightKg: numberOrNull(data.get("currentWeightKg"), 20, 400),
      targetWeightKg: numberOrNull(data.get("targetWeightKg"), 20, 400),
      targetBodyFatPct: numberOrNull(data.get("targetBodyFatPct"), 1, 70),
      dailyProteinG: numberOrNull(data.get("dailyProteinG"), 0, 1000),
      dailyWaterMl: numberOrNull(data.get("dailyWaterMl"), 0, 15000),
      sleepGoalHours: numberOrNull(data.get("sleepGoalHours"), 0, 24),
      weeklyStrengthGoal: integerOrNull(data.get("weeklyStrengthGoal"), 0, 14),
      weeklyBoxingGoal: integerOrNull(data.get("weeklyBoxingGoal"), 0, 14),
      note: cleanText(data.get("goalNote"), 2000),
      updatedAt: new Date().toISOString(),
    });
    await commitSnapshot({ profile, entries: snapshot.entries }, "目标设置已保存。");
  }

  function changeDate(date) {
    if (!isDateKey(date)) return;
    selectedDate = date;
    saveSelectedDate(selectedDate);
    notice = null;
    render();
  }

  function render() {
    const entry = selectedEntry();
    const profile = snapshot.profile;
    const range = recentDateRange(selectedDate, 7);
    const recentEntries = range.map((date) => activeEntries(snapshot.entries).find((item) => item.date === date) || null);
    const overview = dailyOverview(entry, profile);
    const trend = trendSummary(recentEntries, profile);
    const controlsDisabled = storageLoading || saving || !storageReady;

    root.innerHTML = `
      <header class="topbar healthTopbar">
        <div>
          <p class="eyebrow">Health Desk</p>
          <h1>健康</h1>
        </div>
        <div class="healthDateNav" aria-label="健康记录日期">
          <button class="secondaryButton" data-health-date="${escapeAttr(addDays(selectedDate, -1))}" type="button" aria-label="前一天">←</button>
          <input id="health-date-picker" type="date" value="${escapeAttr(selectedDate)}" />
          <button class="secondaryButton" data-health-date="${escapeAttr(todayKey())}" type="button">今天</button>
          <button class="secondaryButton" data-health-date="${escapeAttr(addDays(selectedDate, 1))}" type="button" aria-label="后一天">→</button>
        </div>
      </header>

      <div class="paperStatusBar healthStatusBar" role="status" aria-live="polite" aria-atomic="true">
        <span>${escapeHtml(formatFullDate(selectedDate))}</span>
        <span>${storageLoading ? "正在连接共享数据" : storageReady ? `共享存储已连接 · revision ${revision}` : "浏览器备份 · 只读"}</span>
      </div>

      <main class="healthWorkspace">
        ${renderNotice(notice)}

        <section class="healthOverviewSection" aria-labelledby="health-overview-title">
          <div class="healthSectionHeader">
            <div>
              <p class="healthSectionKicker">${selectedDate === todayKey() ? "TODAY" : "DAILY REVIEW"}</p>
              <h2 id="health-overview-title">${selectedDate === todayKey() ? "今日概览" : `${formatShortDate(selectedDate)}概览`}</h2>
            </div>
            <span class="healthCompletionBadge">完成 ${overview.doneCount} / 5</span>
          </div>
          <div class="healthOverviewGrid">
            ${renderOverviewCard("体重", formatMetric(entry.weightKg, "kg"), weightProgressText(entry, profile), "weight")}
            ${renderOverviewCard("力量 / 拳击", `${entry.training.strengthDone ? "✓" : "–"} / ${entry.training.boxingDone ? "✓" : "–"}`, trainingMinutesText(entry), "training")}
            ${renderOverviewCard("蛋白 / 饮水", `${formatMetric(entry.nutrition.proteinG, "g")} / ${formatWater(entry.nutrition.waterMl)}`, nutritionProgressText(entry, profile), "nutrition")}
            ${renderOverviewCard("睡眠", formatMetric(entry.sleep.durationHours, "小时"), sleepProgressText(entry, profile), "sleep")}
          </div>
          <div class="healthHabitStrip" aria-label="五项每日习惯">
            ${overview.habits.map((habit) => `<span class="healthHabitChip ${habit.done ? "done" : ""}">${habit.done ? "✓" : "○"} ${escapeHtml(habit.label)}</span>`).join("")}
          </div>
        </section>

        <div class="healthMainGrid">
          <section class="healthDailyPanel">
            <div class="healthSectionHeader">
              <div>
                <p class="healthSectionKicker">DAILY LOG</p>
                <h2>每日记录</h2>
              </div>
              <span>${hasEntryData(entry) ? "可继续修改" : "尚未记录"}</span>
            </div>
            <form id="health-daily-form" class="healthDailyForm">
              <fieldset ${controlsDisabled ? "disabled" : ""}>
                <div class="healthFormSection">
                  <div class="healthFormSectionTitle"><span class="healthFormIcon">01</span><div><h3>身体状态</h3><p>每天同一时段记录，趋势更有参考价值</p></div></div>
                  <div class="healthFieldGrid compact">
                    ${renderNumberField("weightKg", "体重", "kg", entry.weightKg, "0.1", "例如 72.5")}
                    ${renderNumberField("waistCm", "腰围", "cm", entry.waistCm, "0.1", "例如 82")}
                    <label class="healthField"><span>精力</span><select name="energyLevel">${renderRatingOptions(entry.energyLevel, "未记录", ["很差", "偏低", "一般", "很好", "状态拉满"])}</select></label>
                  </div>
                </div>

                <div class="healthFormSection">
                  <div class="healthFormSectionTitle"><span class="healthFormIcon">02</span><div><h3>训练打卡</h3><p>力量打基础，拳击练技术，恢复保证持续</p></div></div>
                  <div class="healthTrainingGrid">
                    ${renderTrainingCard("strength", "力量", "增肌与基础力量", entry.training.strengthDone, entry.training.strengthMinutes)}
                    ${renderTrainingCard("boxing", "拳击", "技术、沙袋或实战", entry.training.boxingDone, entry.training.boxingMinutes)}
                    ${renderTrainingCard("recovery", "恢复", "拉伸、散步或筋膜放松", entry.training.recoveryDone, entry.training.recoveryMinutes)}
                  </div>
                  <label class="healthField healthWideField"><span>训练内容 / 拳击复盘</span><textarea name="trainingNote" rows="3" placeholder="动作、组数、重量，或今天拳击最需要改进的一点">${escapeHtml(entry.training.note)}</textarea></label>
                </div>

                <div class="healthFormSection">
                  <div class="healthFormSectionTitle"><span class="healthFormIcon">03</span><div><h3>饮食与饮水</h3><p>先关注蛋白质、蔬菜和饮水，不追求一次做到完美</p></div></div>
                  <div class="healthFieldGrid">
                    ${renderNumberField("proteinG", "蛋白质", "g", entry.nutrition.proteinG, "1", profile.dailyProteinG ? `目标 ${profile.dailyProteinG}g` : "例如 140")}
                    ${renderNumberField("waterMl", "饮水", "ml", entry.nutrition.waterMl, "50", profile.dailyWaterMl ? `目标 ${profile.dailyWaterMl}ml` : "例如 2500")}
                    ${renderNumberField("vegetableServings", "蔬菜", "份", entry.nutrition.vegetableServings, "0.5", "例如 3")}
                    ${renderNumberField("healthyMeals", "健康正餐", "顿", entry.nutrition.healthyMeals, "1", "例如 3")}
                  </div>
                  <div class="healthWaterQuick" aria-label="快速增加饮水量">
                    <span>快速加水</span>
                    <button class="healthQuickButton" data-add-water="250" type="button">+250 ml</button>
                    <button class="healthQuickButton" data-add-water="500" type="button">+500 ml</button>
                  </div>
                  <label class="healthField healthWideField"><span>饮食备注</span><textarea name="nutritionNote" rows="2" placeholder="今天吃了什么，饥饿感如何，有没有需要调整">${escapeHtml(entry.nutrition.note)}</textarea></label>
                </div>

                <div class="healthFormSection">
                  <div class="healthFormSectionTitle"><span class="healthFormIcon">04</span><div><h3>睡眠</h3><p>把这条记录归到醒来的这一天</p></div></div>
                  <div class="healthFieldGrid">
                    ${renderNumberField("sleepHours", "睡眠时长", "小时", entry.sleep.durationHours, "0.1", profile.sleepGoalHours ? `目标 ${profile.sleepGoalHours} 小时` : "例如 7.5")}
                    <label class="healthField"><span>睡眠质量</span><select name="sleepQuality">${renderRatingOptions(entry.sleep.quality, "未记录", ["很差", "较差", "一般", "不错", "很好"])}</select></label>
                    <label class="healthField"><span>入睡时间</span><input name="bedtime" type="time" value="${escapeAttr(entry.sleep.bedtime)}" /></label>
                    <label class="healthField"><span>起床时间</span><input name="wakeTime" type="time" value="${escapeAttr(entry.sleep.wakeTime)}" /></label>
                  </div>
                </div>

                <label class="healthField healthWideField healthDailyNote"><span>今天的一句话</span><textarea name="dailyNote" rows="2" placeholder="身体感受、情绪，或明天最重要的一件事">${escapeHtml(entry.note)}</textarea></label>
                <div class="healthFormActions">
                  <span>${storageReady ? "保存后可在其他浏览器查看" : "共享数据服务恢复后才能编辑"}</span>
                  <button class="primaryButton" type="submit">${saving ? "保存中…" : hasEntryData(entry) ? "更新这一天" : "保存这一天"}</button>
                </div>
              </fieldset>
            </form>
          </section>

          <aside class="healthGoalPanel">
            <div class="healthSectionHeader">
              <div><p class="healthSectionKicker">MY TARGET</p><h2>目标设置</h2></div>
            </div>
            <form id="health-goal-form" class="healthGoalForm">
              <fieldset ${controlsDisabled ? "disabled" : ""}>
                <label class="healthField"><span>当前方向</span><select name="focus">${FOCUS_OPTIONS.map(([value, label]) => `<option value="${value}" ${profile.focus === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
                <div class="healthGoalWeightGrid">
                  ${renderNumberField("currentWeightKg", "当前体重", "kg", profile.currentWeightKg, "0.1", "例如 72.5")}
                  ${renderNumberField("targetWeightKg", "目标体重", "kg", profile.targetWeightKg, "0.1", "例如 75")}
                </div>
                ${renderNumberField("targetBodyFatPct", "目标体脂", "%", profile.targetBodyFatPct, "0.1", "选填")}
                <div class="healthGoalDivider"><span>每日底线</span></div>
                ${renderNumberField("dailyProteinG", "蛋白质目标", "g", profile.dailyProteinG, "1", "例如 140")}
                ${renderNumberField("dailyWaterMl", "饮水目标", "ml", profile.dailyWaterMl, "50", "例如 2500")}
                ${renderNumberField("sleepGoalHours", "睡眠目标", "小时", profile.sleepGoalHours, "0.1", "例如 8")}
                <div class="healthGoalDivider"><span>每周训练</span></div>
                <div class="healthGoalWeightGrid">
                  ${renderNumberField("weeklyStrengthGoal", "力量", "次", profile.weeklyStrengthGoal, "1", "例如 3")}
                  ${renderNumberField("weeklyBoxingGoal", "拳击", "次", profile.weeklyBoxingGoal, "1", "例如 2")}
                </div>
                <label class="healthField healthWideField"><span>目标备注</span><textarea name="goalNote" rows="3" placeholder="比如：先稳定训练和睡眠，不追求快速掉秤">${escapeHtml(profile.note)}</textarea></label>
                <button class="secondaryButton healthGoalSubmit" type="submit">保存目标</button>
              </fieldset>
            </form>
          </aside>
        </div>

        <section class="healthTrendPanel">
          <div class="healthSectionHeader">
            <div><p class="healthSectionKicker">LAST 7 DAYS</p><h2>最近 7 日趋势</h2></div>
            <span>${formatShortDate(range[0])} — ${formatShortDate(range.at(-1))}</span>
          </div>
          <div class="healthTrendSummary">
            ${renderTrendMetric("体重变化", trend.weightChange, trend.weightHint)}
            ${renderTrendMetric("力量 / 拳击", `${trend.strengthCount} / ${trend.boxingCount} 次`, weeklyTrainingHint(profile))}
            ${renderTrendMetric("平均蛋白", trend.averageProtein, profile.dailyProteinG ? `目标 ${profile.dailyProteinG}g` : "设置目标后可对照")}
            ${renderTrendMetric("平均睡眠", trend.averageSleep, profile.sleepGoalHours ? `目标 ${profile.sleepGoalHours} 小时` : "设置目标后可对照")}
          </div>
          <div class="healthTrendStrip">
            ${range.map((date, index) => renderTrendDay(date, recentEntries[index], profile, date === selectedDate)).join("")}
          </div>
          <div class="healthHistoryList">
            <div class="healthHistoryHeader"><span>日期</span><span>身体</span><span>训练</span><span>饮食</span><span>睡眠</span></div>
            ${range.slice().reverse().map((date) => renderHistoryRow(date, activeEntries(snapshot.entries).find((item) => item.date === date), profile)).join("")}
          </div>
        </section>
      </main>
    `;

    bindEvents();
  }

  function bindEvents() {
    root.querySelector("#health-date-picker")?.addEventListener("change", (event) => changeDate(event.target.value));
    root.querySelectorAll("[data-health-date]").forEach((button) => {
      button.addEventListener("click", () => changeDate(button.dataset.healthDate));
    });
    root.querySelector("#health-daily-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      void saveDailyEntry(event.currentTarget);
    });
    root.querySelector("#health-goal-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      void saveGoals(event.currentTarget);
    });
    root.querySelectorAll("[data-add-water]").forEach((button) => {
      button.addEventListener("click", () => {
        const input = root.querySelector('input[name="waterMl"]');
        if (!input || input.disabled) return;
        input.value = String(Math.min(15000, Math.max(0, Number(input.value) || 0) + Number(button.dataset.addWater || 0)));
      });
    });
  }

  render();
  void syncFromSharedStorage();
}

function renderOverviewCard(label, value, hint, tone) {
  return `
    <article class="healthOverviewCard ${escapeAttr(tone)}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <small>${escapeHtml(hint)}</small>
    </article>
  `;
}

function renderNumberField(name, label, unit, value, step, placeholder) {
  return `
    <label class="healthField">
      <span>${escapeHtml(label)}</span>
      <div class="healthInputWithUnit">
        <input name="${escapeAttr(name)}" type="number" min="0" step="${escapeAttr(step)}" value="${escapeAttr(value ?? "")}" placeholder="${escapeAttr(placeholder)}" inputmode="decimal" />
        <em>${escapeHtml(unit)}</em>
      </div>
    </label>
  `;
}

function renderTrainingCard(key, label, description, done, minutes) {
  return `
    <div class="healthTrainingCard ${done ? "done" : ""}">
      <label class="healthTrainingCheck">
        <input name="${key}Done" type="checkbox" ${done ? "checked" : ""} />
        <span class="healthCheckMark">${done ? "✓" : ""}</span>
        <span><strong>${escapeHtml(label)}</strong><small>${escapeHtml(description)}</small></span>
      </label>
      <div class="healthTrainingMinutes">
        <input name="${key}Minutes" type="number" min="0" max="600" step="5" value="${escapeAttr(minutes ?? "")}" placeholder="0" inputmode="numeric" />
        <span>分钟</span>
      </div>
    </div>
  `;
}

function renderRatingOptions(selectedValue, emptyLabel, labels) {
  return `<option value="">${escapeHtml(emptyLabel)}</option>${labels
    .map((label, index) => `<option value="${index + 1}" ${Number(selectedValue) === index + 1 ? "selected" : ""}>${index + 1} · ${escapeHtml(label)}</option>`)
    .join("")}`;
}

function renderNotice(notice) {
  if (!notice?.message) return "";
  return `<div class="healthNotice ${escapeAttr(notice.state || "")}" role="status" aria-live="polite" aria-atomic="true">${escapeHtml(notice.message)}</div>`;
}

function renderTrendMetric(label, value, hint) {
  return `<div class="healthTrendMetric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(hint)}</small></div>`;
}

function renderTrendDay(date, entry, profile, selected) {
  const sleepPercent = entry?.sleep.durationHours && profile.sleepGoalHours
    ? Math.min(100, (entry.sleep.durationHours / profile.sleepGoalHours) * 100)
    : entry?.sleep.durationHours ? Math.min(100, (entry.sleep.durationHours / 8) * 100) : 0;
  const trainingDone = Boolean(entry?.training.strengthDone || entry?.training.boxingDone);
  return `
    <button class="healthTrendDay ${selected ? "selected" : ""} ${entry ? "hasData" : ""}" data-health-date="${escapeAttr(date)}" type="button">
      <span>${escapeHtml(formatWeekday(date))}</span>
      <strong>${escapeHtml(String(parseDate(date).getDate()))}</strong>
      <span class="healthTrendBar" title="睡眠完成度"><i style="height:${escapeAttr(sleepPercent.toFixed(0))}%"></i></span>
      <small>${trainingDone ? "训练 ✓" : entry ? "已记录" : "—"}</small>
    </button>
  `;
}

function renderHistoryRow(date, entry, profile) {
  const training = entry
    ? [entry.training.strengthDone ? "力量" : "", entry.training.boxingDone ? "拳击" : "", entry.training.recoveryDone ? "恢复" : ""].filter(Boolean).join(" · ") || "休息"
    : "—";
  const nutrition = entry
    ? `${formatMetric(entry.nutrition.proteinG, "g 蛋白")} · ${formatWater(entry.nutrition.waterMl)}`
    : "—";
  const sleep = entry ? formatMetric(entry.sleep.durationHours, "小时") : "—";
  const body = entry ? formatMetric(entry.weightKg, "kg") : "—";
  const score = entry ? dailyOverview(entry, profile).doneCount : 0;
  return `
    <button class="healthHistoryRow ${entry ? "hasData" : ""}" data-health-date="${escapeAttr(date)}" type="button">
      <span><strong>${escapeHtml(formatShortDate(date))}</strong><small>${escapeHtml(formatWeekday(date))}${entry ? ` · ${score}/5` : ""}</small></span>
      <span>${escapeHtml(body)}</span>
      <span>${escapeHtml(training)}</span>
      <span>${escapeHtml(nutrition)}</span>
      <span>${escapeHtml(sleep)}</span>
    </button>
  `;
}

function dailyOverview(entry, profile) {
  const habits = [
    { label: "力量", done: entry.training.strengthDone },
    { label: "拳击", done: entry.training.boxingDone },
    { label: "健康饮食", done: healthyNutritionDone(entry, profile) },
    { label: "饮水", done: goalReached(entry.nutrition.waterMl, profile.dailyWaterMl, 2000) },
    { label: "睡眠", done: goalReached(entry.sleep.durationHours, profile.sleepGoalHours, 7) },
  ];
  return { habits, doneCount: habits.filter((habit) => habit.done).length };
}

function healthyNutritionDone(entry, profile) {
  const proteinDone = goalReached(entry.nutrition.proteinG, profile.dailyProteinG, null);
  const mealsDone = Number(entry.nutrition.healthyMeals) >= 2;
  const vegetablesDone = Number(entry.nutrition.vegetableServings) >= 2;
  return profile.dailyProteinG ? proteinDone && (mealsDone || vegetablesDone) : mealsDone || vegetablesDone;
}

function goalReached(value, goal, fallback) {
  const target = positiveNumber(goal) ? Number(goal) : positiveNumber(fallback) ? Number(fallback) : null;
  return target !== null && Number(value) >= target;
}

function trendSummary(entries, profile) {
  const active = entries.filter(Boolean);
  const weights = active.map((entry) => entry.weightKg).filter(positiveNumber);
  const protein = active.map((entry) => entry.nutrition.proteinG).filter((value) => value !== null);
  const sleep = active.map((entry) => entry.sleep.durationHours).filter((value) => value !== null);
  const weightDelta = weights.length >= 2 ? weights.at(-1) - weights[0] : null;
  return {
    weightChange: weightDelta === null ? "记录两次后显示" : `${weightDelta > 0 ? "+" : ""}${round(weightDelta, 1)} kg`,
    weightHint: weights.length ? `${weights.length} 天有体重记录` : "还没有体重记录",
    strengthCount: active.filter((entry) => entry.training.strengthDone).length,
    boxingCount: active.filter((entry) => entry.training.boxingDone).length,
    averageProtein: protein.length ? `${round(average(protein), 0)} g` : "—",
    averageSleep: sleep.length ? `${round(average(sleep), 1)} 小时` : "—",
    profile,
  };
}

function weeklyTrainingHint(profile) {
  const strength = profile.weeklyStrengthGoal;
  const boxing = profile.weeklyBoxingGoal;
  if (strength !== null || boxing !== null) return `目标 ${strength ?? "—"} / ${boxing ?? "—"} 次`;
  return "设置每周目标后可对照";
}

function weightProgressText(entry, profile) {
  const weight = entry.weightKg ?? profile.currentWeightKg;
  if (!positiveNumber(weight)) return "记录体重后显示趋势";
  if (!positiveNumber(profile.targetWeightKg)) return "可在右侧设置目标体重";
  const difference = Number(profile.targetWeightKg) - Number(weight);
  if (Math.abs(difference) < 0.05) return "已到目标体重";
  return `距离目标 ${Math.abs(round(difference, 1))} kg`;
}

function trainingMinutesText(entry) {
  const total = [entry.training.strengthMinutes, entry.training.boxingMinutes, entry.training.recoveryMinutes]
    .filter((value) => value !== null)
    .reduce((sum, value) => sum + Number(value), 0);
  return total ? `合计 ${total} 分钟` : "力量 / 拳击完成情况";
}

function nutritionProgressText(entry, profile) {
  const labels = [];
  if (entry.nutrition.proteinG !== null && profile.dailyProteinG) labels.push(`${Math.min(100, Math.round((entry.nutrition.proteinG / profile.dailyProteinG) * 100))}% 蛋白目标`);
  if (entry.nutrition.waterMl !== null && profile.dailyWaterMl) labels.push(`${Math.min(100, Math.round((entry.nutrition.waterMl / profile.dailyWaterMl) * 100))}% 饮水目标`);
  return labels.join(" · ") || "记录后与每日目标对照";
}

function sleepProgressText(entry, profile) {
  if (entry.sleep.quality) return `质量 ${entry.sleep.quality} / 5`;
  if (entry.sleep.durationHours !== null && profile.sleepGoalHours) return `目标 ${profile.sleepGoalHours} 小时`;
  return "时长与质量都值得记录";
}

function formatMetric(value, unit) {
  return value === null || value === undefined || value === "" ? "—" : `${round(Number(value), 1)} ${unit}`;
}

function formatWater(value) {
  if (value === null || value === undefined || value === "") return "—";
  return Number(value) >= 1000 ? `${round(Number(value) / 1000, 1)} L` : `${round(Number(value), 0)} ml`;
}

function mergeSnapshots(preferredSnapshot, otherSnapshot) {
  const preferred = normalizeSnapshot(preferredSnapshot);
  const other = normalizeSnapshot(otherSnapshot);
  return {
    profile: mergeProfile(preferred.profile, other.profile),
    entries: mergeEntries(preferred.entries, other.entries),
  };
}

function mergeProfile(preferred, other) {
  const preferredTime = timestamp(preferred.updatedAt);
  const otherTime = timestamp(other.updatedAt);
  if (preferredTime > otherTime) return normalizeProfile(preferred);
  if (otherTime > preferredTime) return normalizeProfile(other);
  return hasProfileData(other) ? normalizeProfile(other) : normalizeProfile(preferred);
}

function mergeEntries(preferredEntries, otherEntries) {
  const byDate = new Map();
  normalizeEntries(preferredEntries).forEach((entry) => byDate.set(entry.date, entry));
  normalizeEntries(otherEntries).forEach((entry) => {
    const current = byDate.get(entry.date);
    if (!current || recordTimestamp(entry) >= recordTimestamp(current)) byDate.set(entry.date, entry);
  });
  return [...byDate.values()].sort((left, right) => left.date.localeCompare(right.date));
}

function upsertEntry(entries, nextEntry) {
  return mergeEntries([nextEntry], entries);
}

function recordTimestamp(entry) {
  return Math.max(timestamp(entry.updatedAt), timestamp(entry.deletedAt), timestamp(entry.createdAt));
}

function normalizeSnapshot(value) {
  return {
    profile: normalizeProfile(value?.profile),
    entries: normalizeEntries(value?.entries),
  };
}

function normalizeProfile(profile) {
  const focus = FOCUS_OPTIONS.some(([value]) => value === profile?.focus) ? profile.focus : "recomposition";
  return {
    focus,
    currentWeightKg: numberOrNull(profile?.currentWeightKg, 20, 400),
    targetWeightKg: numberOrNull(profile?.targetWeightKg, 20, 400),
    targetBodyFatPct: numberOrNull(profile?.targetBodyFatPct, 1, 70),
    dailyProteinG: numberOrNull(profile?.dailyProteinG, 0, 1000),
    dailyWaterMl: numberOrNull(profile?.dailyWaterMl, 0, 15000),
    sleepGoalHours: numberOrNull(profile?.sleepGoalHours, 0, 24),
    weeklyStrengthGoal: integerOrNull(profile?.weeklyStrengthGoal, 0, 14),
    weeklyBoxingGoal: integerOrNull(profile?.weeklyBoxingGoal, 0, 14),
    note: cleanText(profile?.note, 2000),
    updatedAt: normalizeIsoDate(profile?.updatedAt),
  };
}

function normalizeEntries(entries) {
  if (!Array.isArray(entries)) return [];
  const byDate = new Map();
  entries.forEach((rawEntry) => {
    const entry = normalizeEntry(rawEntry);
    if (!entry) return;
    const current = byDate.get(entry.date);
    if (!current || recordTimestamp(entry) >= recordTimestamp(current)) byDate.set(entry.date, entry);
  });
  return [...byDate.values()].sort((left, right) => left.date.localeCompare(right.date));
}

function normalizeEntry(entry) {
  if (!isDateKey(entry?.date)) return null;
  return {
    id: cleanText(entry.id, 200) || `health-day:${entry.date}`,
    date: entry.date,
    weightKg: numberOrNull(entry.weightKg, 20, 400),
    waistCm: numberOrNull(entry.waistCm, 30, 250),
    energyLevel: integerOrNull(entry.energyLevel, 1, 5),
    training: {
      strengthDone: Boolean(entry.training?.strengthDone),
      strengthMinutes: numberOrNull(entry.training?.strengthMinutes, 0, 600),
      boxingDone: Boolean(entry.training?.boxingDone),
      boxingMinutes: numberOrNull(entry.training?.boxingMinutes, 0, 600),
      recoveryDone: Boolean(entry.training?.recoveryDone),
      recoveryMinutes: numberOrNull(entry.training?.recoveryMinutes, 0, 600),
      note: cleanText(entry.training?.note, 2000),
    },
    nutrition: {
      proteinG: numberOrNull(entry.nutrition?.proteinG, 0, 1000),
      waterMl: numberOrNull(entry.nutrition?.waterMl, 0, 15000),
      vegetableServings: numberOrNull(entry.nutrition?.vegetableServings, 0, 30),
      healthyMeals: integerOrNull(entry.nutrition?.healthyMeals, 0, 12),
      note: cleanText(entry.nutrition?.note, 2000),
    },
    sleep: {
      durationHours: numberOrNull(entry.sleep?.durationHours, 0, 24),
      quality: integerOrNull(entry.sleep?.quality, 1, 5),
      bedtime: normalizeTime(entry.sleep?.bedtime),
      wakeTime: normalizeTime(entry.sleep?.wakeTime),
    },
    note: cleanText(entry.note, 3000),
    createdAt: normalizeIsoDate(entry.createdAt),
    updatedAt: normalizeIsoDate(entry.updatedAt),
    deletedAt: normalizeIsoDate(entry.deletedAt),
  };
}

function emptySnapshot() {
  return { profile: normalizeProfile(null), entries: [] };
}

function emptyEntry(date) {
  return normalizeEntry({ date, id: `health-day:${date}`, training: {}, nutrition: {}, sleep: {} });
}

function hasHealthData(value) {
  const normalized = normalizeSnapshot(value);
  // Tombstones count as data: dropping a backup that contains only deletions could
  // otherwise let older records reappear during a one-time migration.
  return hasProfileData(normalized.profile) || normalized.entries.length > 0;
}

function hasProfileData(profile) {
  return Boolean(
    profile.updatedAt || profile.currentWeightKg !== null || profile.targetWeightKg !== null || profile.targetBodyFatPct !== null ||
      profile.dailyProteinG !== null || profile.dailyWaterMl !== null || profile.sleepGoalHours !== null ||
      profile.weeklyStrengthGoal !== null || profile.weeklyBoxingGoal !== null || profile.note,
  );
}

function hasEntryData(entry) {
  if (!entry || entry.deletedAt) return false;
  return Boolean(
    entry.updatedAt || entry.weightKg !== null || entry.waistCm !== null || entry.energyLevel !== null ||
      entry.training.strengthDone || entry.training.boxingDone || entry.training.recoveryDone ||
      entry.training.strengthMinutes !== null || entry.training.boxingMinutes !== null || entry.training.recoveryMinutes !== null ||
      entry.training.note || entry.nutrition.proteinG !== null || entry.nutrition.waterMl !== null ||
      entry.nutrition.vegetableServings !== null || entry.nutrition.healthyMeals !== null || entry.nutrition.note ||
      entry.sleep.durationHours !== null || entry.sleep.quality !== null || entry.sleep.bedtime || entry.sleep.wakeTime || entry.note,
  );
}

function activeEntries(entries) {
  return normalizeEntries(entries).filter((entry) => !entry.deletedAt);
}

async function fetchHealthStorage() {
  const response = await fetch(HEALTH_STORAGE_ENDPOINT, { cache: "no-store" });
  const payload = await readJsonResponse(response);
  if (!response.ok || !payload?.ok || !Array.isArray(payload.entries)) {
    const error = new Error(payload?.error || "health_storage_read_failed");
    error.code = payload?.error || "health_storage_read_failed";
    throw error;
  }
  return {
    ...normalizeSnapshot(payload),
    ok: true,
    initialized: Boolean(payload.initialized),
    revision: normalizeRevision(payload.revision),
    updatedAt: normalizeIsoDate(payload.updatedAt),
  };
}

async function writeHealthStorage(nextSnapshot, baseRevision) {
  const normalized = normalizeSnapshot(nextSnapshot);
  const response = await fetch(HEALTH_STORAGE_ENDPOINT, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      profile: normalized.profile,
      entries: normalized.entries,
      baseRevision: normalizeRevision(baseRevision),
    }),
  });
  const payload = await readJsonResponse(response);
  if (response.status === 409 && payload?.state) {
    const error = new Error("revision_conflict");
    error.code = "revision_conflict";
    error.state = {
      ...normalizeSnapshot(payload.state),
      revision: normalizeRevision(payload.state.revision),
    };
    throw error;
  }
  if (!response.ok || !payload?.ok || !Array.isArray(payload.entries)) {
    const error = new Error(payload?.error || "health_storage_write_failed");
    error.code = payload?.error || "health_storage_write_failed";
    throw error;
  }
  return {
    ...normalizeSnapshot(payload),
    ok: true,
    initialized: true,
    revision: normalizeRevision(payload.revision),
    updatedAt: normalizeIsoDate(payload.updatedAt),
  };
}

async function readJsonResponse(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function loadHealthBackup() {
  try {
    const raw = localStorage.getItem(HEALTH_BACKUP_KEY);
    if (!raw) return { exists: false, snapshot: emptySnapshot() };
    return { exists: true, snapshot: normalizeSnapshot(JSON.parse(raw)) };
  } catch {
    return { exists: false, snapshot: emptySnapshot() };
  }
}

function saveHealthBackup(value) {
  try {
    localStorage.setItem(HEALTH_BACKUP_KEY, JSON.stringify({ version: 1, ...normalizeSnapshot(value) }));
  } catch {
    // The API remains authoritative; a browser backup is optional.
  }
}

function loadMigrationMarker() {
  try {
    return localStorage.getItem(HEALTH_MIGRATED_KEY) === "1";
  } catch {
    return false;
  }
}

function saveMigrationMarker() {
  try {
    localStorage.setItem(HEALTH_MIGRATED_KEY, "1");
  } catch {
    // A missing marker only causes a safe timestamp-based merge on next load.
  }
}

function loadSelectedDate() {
  try {
    const value = localStorage.getItem(HEALTH_SELECTED_DATE_KEY);
    return isDateKey(value) ? value : todayKey();
  } catch {
    return todayKey();
  }
}

function saveSelectedDate(date) {
  try {
    if (isDateKey(date)) localStorage.setItem(HEALTH_SELECTED_DATE_KEY, date);
  } catch {
    // Selected date is UI state only.
  }
}

function healthStorageErrorMessage(error, fallback) {
  const messages = {
    revision_conflict: "健康数据仍在被其他页面更新，已停止本次保存。请检查后重试。",
    health_storage_read_failed: "健康数据文件暂时无法读取。为保护已有数据，当前页面只读。",
    health_storage_write_failed: "健康数据文件写入失败，本次修改未保存。",
    invalid_health_storage: "健康数据格式不正确，本次修改未保存。",
  };
  return messages[error?.code || error?.message] || fallback;
}

function numberOrNull(value, minimum, maximum) {
  if (value === null || value === undefined || String(value).trim() === "") return null;
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return Math.min(maximum, Math.max(minimum, number));
}

function integerOrNull(value, minimum, maximum) {
  const number = numberOrNull(value, minimum, maximum);
  return number === null ? null : Math.round(number);
}

function positiveNumber(value) {
  return Number.isFinite(Number(value)) && Number(value) > 0;
}

function cleanText(value, maximumLength) {
  return String(value ?? "").trim().slice(0, maximumLength);
}

function normalizeTime(value) {
  const text = String(value ?? "").trim();
  return /^([01]\d|2[0-3]):[0-5]\d$/.test(text) ? text : "";
}

function normalizeIsoDate(value) {
  const text = String(value ?? "").trim();
  return Number.isFinite(Date.parse(text)) ? new Date(text).toISOString() : "";
}

function normalizeRevision(value) {
  const revision = Number(value);
  return Number.isInteger(revision) && revision >= 0 ? revision : 0;
}

function timestamp(value) {
  const parsed = Date.parse(value || "");
  return Number.isFinite(parsed) ? parsed : 0;
}

function average(values) {
  return values.reduce((sum, value) => sum + Number(value), 0) / values.length;
}

function round(value, decimals) {
  const factor = 10 ** decimals;
  return Math.round((Number(value) + Number.EPSILON) * factor) / factor;
}

function recentDateRange(endDate, count) {
  return Array.from({ length: count }, (_, index) => addDays(endDate, index - count + 1));
}

function todayKey() {
  return dateKey(new Date());
}

function addDays(value, amount) {
  const date = parseDate(value);
  date.setDate(date.getDate() + amount);
  return dateKey(date);
}

function dateKey(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function parseDate(value) {
  const [year, month, day] = String(value || "").split("-").map(Number);
  return new Date(year || 1970, (month || 1) - 1, day || 1);
}

function isDateKey(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ""))) return false;
  return dateKey(parseDate(value)) === value;
}

function formatShortDate(date) {
  const parsed = parseDate(date);
  return `${parsed.getMonth() + 1}月${parsed.getDate()}日`;
}

function formatFullDate(date) {
  return `${date} · ${formatShortDate(date)} · ${formatWeekday(date)}`;
}

function formatWeekday(date) {
  return ["周日", "周一", "周二", "周三", "周四", "周五", "周六"][parseDate(date).getDay()];
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}
