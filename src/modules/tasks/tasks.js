const STORAGE_KEY = "ai-workbench:daily-tasks:v1";
const SELECTED_DATE_STORAGE_KEY = "ai-workbench:daily-tasks:selected-date:v1";

export function createTaskWorkspace(root) {
  let tasks = loadTasks();
  let selectedDate = loadSelectedDate();
  let editingId = "";
  let draggedTaskId = "";
  let pointerDragTaskId = "";
  let pointerDragOverId = "";

  function tasksForSelectedDate() {
    return tasks.filter((task) => task.date === selectedDate).sort(sortTasks);
  }

  function addTask(form) {
    const data = new FormData(form);
    const title = String(data.get("title") ?? "").trim();
    if (!title) return;

    tasks = [
      ...tasks,
      {
        id: `task-${Date.now()}-${Math.random().toString(16).slice(2)}`,
        title,
        date: selectedDate,
        done: false,
        note: "",
        order: nextOrderForDate(selectedDate),
        createdAt: new Date().toISOString(),
        completedAt: "",
      },
    ];

    editingId = "";
    saveTasks(tasks);
    render({ focusInput: true });
  }

  function updateTaskTitle(taskId, title) {
    const nextTitle = title.trim();
    if (!nextTitle) return;

    tasks = tasks.map((task) =>
      task.id === taskId
        ? {
            ...task,
            title: nextTitle,
            updatedAt: new Date().toISOString(),
          }
        : task,
    );
    editingId = "";
    saveTasks(tasks);
    render({ focusInput: true });
  }

  function toggleTask(taskId) {
    tasks = tasks.map((task) =>
      task.id === taskId
        ? {
            ...task,
            done: !task.done,
            completedAt: !task.done ? new Date().toISOString() : "",
          }
        : task,
    );
    saveTasks(tasks);
    render({ focusInput: true });
  }

  function deleteTask(taskId) {
    tasks = tasks.filter((task) => task.id !== taskId);
    if (editingId === taskId) editingId = "";
    saveTasks(tasks);
    render({ focusInput: true });
  }

  function moveTask(draggedId, targetId) {
    if (!draggedId || !targetId || draggedId === targetId) return;

    const dayTasks = tasksForSelectedDate();
    const dragged = dayTasks.find((task) => task.id === draggedId);
    const targetIndex = dayTasks.findIndex((task) => task.id === targetId);
    if (!dragged || targetIndex < 0) return;

    const reordered = dayTasks.filter((task) => task.id !== draggedId);
    reordered.splice(targetIndex, 0, dragged);
    const orderById = new Map(reordered.map((task, index) => [task.id, index + 1]));

    tasks = tasks.map((task) =>
      task.date === selectedDate && orderById.has(task.id)
        ? {
            ...task,
            order: orderById.get(task.id),
          }
        : task,
    );
    draggedTaskId = "";
    saveTasks(tasks);
    render({ focusInput: true });
  }

  function selectDate(date) {
    selectedDate = date || todayKey();
    editingId = "";
    saveSelectedDate(selectedDate);
    render({ focusInput: true });
  }

  function startEdit(taskId) {
    editingId = taskId;
    render({ focusEdit: true });
  }

  function cancelEdit() {
    editingId = "";
    render({ focusInput: true });
  }

  function getTaskRowAtPointer(event) {
    const element = document.elementFromPoint(event.clientX, event.clientY);
    const row = element?.closest?.("[data-drag-task]");
    return row && root.contains(row) ? row : null;
  }

  function beginPointerDrag(event, row) {
    if (event.target.closest("button, input, form")) return;
    pointerDragTaskId = row.dataset.dragTask;
    pointerDragOverId = row.dataset.dragTask;
    row.classList.add("dragging");
    event.preventDefault();
    document.addEventListener("pointermove", handlePointerMove);
    document.addEventListener("pointerup", handlePointerUp);
    document.addEventListener("pointercancel", handlePointerCancel);
  }

  function handlePointerMove(event) {
    const row = getTaskRowAtPointer(event);
    const overId = row?.dataset.dragTask || "";
    if (overId === pointerDragOverId) return;

    root.querySelectorAll(".taskSimpleRow.dragTarget").forEach((item) => item.classList.remove("dragTarget"));
    pointerDragOverId = overId;
    if (row && overId !== pointerDragTaskId) row.classList.add("dragTarget");
  }

  function handlePointerUp(event) {
    const sourceId = pointerDragTaskId;
    const targetId = getTaskRowAtPointer(event)?.dataset.dragTask || pointerDragOverId;
    clearPointerDrag();
    if (sourceId && targetId && sourceId !== targetId) {
      moveTask(sourceId, targetId);
    }
  }

  function handlePointerCancel() {
    clearPointerDrag();
  }

  function clearPointerDrag() {
    pointerDragTaskId = "";
    pointerDragOverId = "";
    document.removeEventListener("pointermove", handlePointerMove);
    document.removeEventListener("pointerup", handlePointerUp);
    document.removeEventListener("pointercancel", handlePointerCancel);
    root.querySelectorAll(".taskSimpleRow.dragging, .taskSimpleRow.dragTarget").forEach((item) => {
      item.classList.remove("dragging", "dragTarget");
    });
  }

  function nextOrderForDate(date) {
    const orders = tasks
      .filter((task) => task.date === date)
      .map((task) => Number(task.order))
      .filter(Number.isFinite);
    return orders.length ? Math.max(...orders) + 1 : 1;
  }

  function render(options = {}) {
    const dayTasks = tasksForSelectedDate();
    const doneCount = dayTasks.filter((task) => task.done).length;
    const openCount = dayTasks.length - doneCount;

    root.innerHTML = `
      <header class="topbar">
        <div>
          <p class="eyebrow">Daily Desk</p>
          <h1>每日待办</h1>
        </div>
        <button class="primaryButton" id="today-date" type="button"><span>创建今日</span><span>${escapeHtml(formatMonthDay(todayKey()))}</span></button>
      </header>

      <div class="paperStatusBar">
        <span>${escapeHtml(formatDateTitle(selectedDate))}</span>
        <span>${dayTasks.length} 条待办 · 未完成 ${openCount} 条 · 已完成 ${doneCount} 条</span>
      </div>

      <div class="taskSimpleLayout">
        <section class="taskQuickPane">
          <div class="taskDateTools">
            <label>
              <span>当前日期</span>
              <input id="task-date-picker" type="date" value="${escapeAttr(selectedDate)}" />
            </label>
            <div class="taskSelectedDate">
              <strong>${escapeHtml(formatDateTitle(selectedDate))}</strong>
              <span>${escapeHtml(formatWeekday(selectedDate))}</span>
            </div>
          </div>

          <form class="taskQuickAdd" id="task-form">
            <input name="title" required autocomplete="off" placeholder="直接输入待办，按 Enter 添加，比如 12345" />
            <button class="primaryButton" type="submit"><span>+</span><span>添加</span></button>
          </form>

          <div class="taskDayList">
            ${
              dayTasks.length
                ? dayTasks.map((task, index) => renderTaskRow(task, index + 1, editingId)).join("")
                : `<div class="taskEmptyHint">这一天还没有待办。点“创建今日”或选择日期后，直接输入就行。</div>`
            }
          </div>
        </section>

        <aside class="taskDatePane">
          <h2>日期</h2>
          <div class="taskDateList">
            ${renderDateButtons()}
          </div>
        </aside>
      </div>
    `;

    bindEvents();
    if (options.focusInput) {
      root.querySelector('input[name="title"]')?.focus();
    }
    if (options.focusEdit && editingId) {
      const input = root.querySelector(`[data-edit-input="${editingId}"]`);
      input?.focus();
      input?.select();
    }
  }

  function bindEvents() {
    root.querySelector("#today-date")?.addEventListener("click", () => {
      selectDate(todayKey());
    });

    root.querySelector("#task-date-picker")?.addEventListener("change", (event) => {
      selectDate(event.target.value || todayKey());
    });

    root.querySelector("#task-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      addTask(event.currentTarget);
    });

    root.querySelectorAll("[data-date]").forEach((button) => {
      button.addEventListener("click", () => {
        selectDate(button.dataset.date);
      });
    });

    root.querySelectorAll("[data-toggle-task]").forEach((button) => {
      button.addEventListener("click", () => {
        toggleTask(button.dataset.toggleTask);
      });
    });

    root.querySelectorAll("[data-delete-task]").forEach((button) => {
      button.addEventListener("click", () => {
        deleteTask(button.dataset.deleteTask);
      });
    });

    root.querySelectorAll("[data-edit-task]").forEach((button) => {
      button.addEventListener("click", () => {
        startEdit(button.dataset.editTask);
      });
    });

    root.querySelectorAll("[data-cancel-edit]").forEach((button) => {
      button.addEventListener("click", () => {
        cancelEdit();
      });
    });

    root.querySelectorAll("[data-edit-form]").forEach((form) => {
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        updateTaskTitle(form.dataset.editForm, String(new FormData(form).get("title") ?? ""));
      });
    });

    root.querySelectorAll("[data-edit-input]").forEach((input) => {
      input.addEventListener("keydown", (event) => {
        if (event.key === "Escape") cancelEdit();
      });
    });

    root.querySelectorAll("[data-drag-task]").forEach((row) => {
      row.addEventListener("dragstart", (event) => {
        draggedTaskId = row.dataset.dragTask;
        row.classList.add("dragging");
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", draggedTaskId);
      });

      row.addEventListener("dragend", () => {
        draggedTaskId = "";
        row.classList.remove("dragging");
      });

      row.addEventListener("dragover", (event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
      });

      row.addEventListener("drop", (event) => {
        event.preventDefault();
        moveTask(draggedTaskId || event.dataTransfer.getData("text/plain"), row.dataset.dragTask);
      });

      row.addEventListener("pointerdown", (event) => {
        beginPointerDrag(event, row);
      });
    });
  }

  function renderDateButtons() {
    const dates = uniqueDates(tasks);
    if (!dates.includes(selectedDate)) dates.unshift(selectedDate);

    return dates
      .map((date) => {
        const count = tasks.filter((task) => task.date === date).length;
        return `
          <button class="taskDateNav ${date === selectedDate ? "selected" : ""}" data-date="${escapeAttr(date)}" type="button">
            <span>
              <strong>${escapeHtml(formatMonthDay(date))}</strong>
              <small>${escapeHtml(formatWeekday(date))}</small>
            </span>
            <em>${count} 条</em>
          </button>
        `;
      })
      .join("");
  }

  render();
}

function renderTaskRow(task, index, editingId) {
  const isEditing = task.id === editingId;
  return `
    <div class="taskSimpleRow ${task.done ? "done" : ""} ${isEditing ? "editing" : ""}" data-drag-task="${escapeAttr(task.id)}" draggable="${isEditing ? "false" : "true"}">
      <span class="taskDragHandle" title="拖动排序">↕</span>
      <button class="taskSimpleCheck" data-toggle-task="${escapeAttr(task.id)}" type="button" aria-label="${task.done ? "标记为未完成" : "标记为完成"}">${task.done ? "✓" : ""}</button>
      <div class="taskSimpleBody">
        ${
          isEditing
            ? `
              <form class="taskEditForm" data-edit-form="${escapeAttr(task.id)}">
                <input name="title" data-edit-input="${escapeAttr(task.id)}" value="${escapeAttr(task.title)}" />
              <button class="secondaryButton" type="submit" aria-label="保存待办修改">保存</button>
              <button class="secondaryButton" data-cancel-edit="${escapeAttr(task.id)}" type="button" aria-label="取消修改">取消</button>
              </form>
            `
            : `
              <strong title="${escapeAttr(task.title)}">${index}. ${escapeHtml(task.title)}</strong>
              <span>${escapeHtml(task.date)}${task.done ? " · 已完成" : ""}</span>
            `
        }
      </div>
      ${
        isEditing
          ? ""
          : `
            <div class="taskSimpleActions">
              <button class="taskEditButton" data-edit-task="${escapeAttr(task.id)}" type="button" aria-label="修改待办">修改</button>
              <button class="taskDeleteButton" data-delete-task="${escapeAttr(task.id)}" type="button" aria-label="删除待办">删除</button>
            </div>
          `
      }
    </div>
  `;
}

function sortTasks(a, b) {
  const orderA = Number(a.order);
  const orderB = Number(b.order);
  if (Number.isFinite(orderA) && Number.isFinite(orderB) && orderA !== orderB) return orderA - orderB;
  return (a.createdAt || "").localeCompare(b.createdAt || "");
}

function uniqueDates(tasks) {
  return Array.from(new Set(tasks.map((task) => task.date || todayKey()))).sort((a, b) => b.localeCompare(a));
}

function todayKey() {
  return dateKey(new Date());
}

function dateKey(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function formatDateTitle(date) {
  const parsed = parseDate(date);
  return `${date} · ${parsed.getMonth() + 1}月${parsed.getDate()}日 · ${formatWeekday(date)}`;
}

function formatMonthDay(date) {
  const parsed = parseDate(date);
  return `${parsed.getMonth() + 1}月${parsed.getDate()}日`;
}

function formatWeekday(date) {
  return ["周日", "周一", "周二", "周三", "周四", "周五", "周六"][parseDate(date).getDay()];
}

function parseDate(value) {
  const [year, month, day] = String(value || todayKey()).split("-").map(Number);
  return new Date(year || 1970, (month || 1) - 1, day || 1);
}

function loadTasks() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    if (!Array.isArray(parsed)) return [];
    return ensureOrder(parsed.map(normalizeTask).filter(Boolean));
  } catch {
    return [];
  }
}

function normalizeTask(task) {
  if (!task?.title) return null;
  return {
    ...task,
    id: task.id || `task-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    title: String(task.title),
    date: task.date || todayKey(),
    done: Boolean(task.done),
    note: task.note || "",
    createdAt: task.createdAt || new Date().toISOString(),
    completedAt: task.completedAt || "",
  };
}

function ensureOrder(items) {
  const nextOrderByDate = {};
  return items.map((task) => {
    const date = task.date || todayKey();
    const order = Number(task.order);
    if (Number.isFinite(order)) {
      nextOrderByDate[date] = Math.max(nextOrderByDate[date] ?? 0, order);
      return {
        ...task,
        order,
      };
    }

    nextOrderByDate[date] = (nextOrderByDate[date] ?? 0) + 1;
    return {
      ...task,
      order: nextOrderByDate[date],
    };
  });
}

function saveTasks(tasks) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(tasks));
}

function loadSelectedDate() {
  try {
    const savedDate = localStorage.getItem(SELECTED_DATE_STORAGE_KEY);
    return isDateKey(savedDate) ? savedDate : todayKey();
  } catch {
    return todayKey();
  }
}

function saveSelectedDate(date) {
  try {
    if (isDateKey(date)) localStorage.setItem(SELECTED_DATE_STORAGE_KEY, date);
  } catch {
  }
}

function isDateKey(value) {
  return /^\d{4}-\d{2}-\d{2}$/.test(String(value ?? ""));
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
