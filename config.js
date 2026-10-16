const tasks = document.querySelector("#tasks");
const template = document.querySelector("#task-template");
const status = document.querySelector("#status");
const saveButton = document.querySelector("#save-config");
const emailEnabled = document.querySelector("#email-enabled");
const qqAddress = document.querySelector("#qq-address");
const emailRecipient = document.querySelector("#email-recipient");
const authorizationCode = document.querySelector("#authorization-code");
const dailyNoteEnabled = document.querySelector("#daily-note-enabled");
const dailyNoteCity = document.querySelector("#daily-note-city");
let dirty = false;

function setStatus(message, error = false) {
  status.textContent = message;
  status.classList.toggle("error", error);
}

function newId() {
  return `task-${crypto.randomUUID()}`;
}

function updateCount() {
  const cards = [...tasks.querySelectorAll(".task-card")];
  document.querySelector("#task-count").textContent = String(cards.length);
  document.querySelector("#empty-state").hidden = cards.length !== 0;
  cards.forEach((card, index) => {
    card.querySelector(".task-number").textContent = String(index + 1);
  });
}

function addTask(item = {}) {
  const card = template.content.firstElementChild.cloneNode(true);
  card.dataset.id = item.id || newId();
  card.querySelector(".name").value = item.name || "";
  card.querySelector(".message").value = item.message || "";
  card.querySelector(".send-at").value = item.send_at || "20:30";
  card.querySelector(".enabled").checked = item.enabled !== false;
  card.querySelector(".remove-task").addEventListener("click", () => {
    card.remove();
    dirty = true;
    updateCount();
    setStatus("有未保存的更改");
  });
  card.addEventListener("input", () => {
    dirty = true;
    setStatus("有未保存的更改");
  });
  card.addEventListener("change", () => {
    dirty = true;
    setStatus("有未保存的更改");
  });
  tasks.appendChild(card);
  updateCount();
  return card;
}

function collectTasks() {
  const result = [];
  for (const card of tasks.querySelectorAll(".task-card")) {
    const name = card.querySelector(".name");
    const message = card.querySelector(".message");
    const sendAt = card.querySelector(".send-at");
    if (!name.value.trim()) {
      name.focus();
      throw new Error("请填写好友昵称");
    }
    if (!message.value.trim()) {
      message.focus();
      throw new Error("请填写发送内容");
    }
    if (!sendAt.value) {
      sendAt.focus();
      throw new Error("请选择发送时间");
    }
    result.push({
      id: card.dataset.id,
      name: name.value.trim(),
      message: message.value,
      send_at: sendAt.value,
      enabled: card.querySelector(".enabled").checked,
    });
  }
  return result;
}

async function loadConfig() {
  try {
    const response = await fetch("/api/config", {cache: "no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "读取配置失败");
    if (!Array.isArray(data.friends)) throw new Error("配置文件缺少 friends 列表");
    let addedIds = false;
    data.friends.forEach(item => {
      if (!item.id) addedIds = true;
      addTask(item);
    });
    const email = data.email || {};
    emailEnabled.checked = email.enabled === true;
    qqAddress.value = email.qq_address || "";
    emailRecipient.value = email.recipient || "";
    authorizationCode.value = email.authorization_code || "";
    const dailyNote = data.daily_note || {};
    dailyNoteEnabled.checked = dailyNote.enabled === true;
    dailyNoteCity.value = dailyNote.city || "";
    dirty = addedIds;
    setStatus(addedIds ? "旧配置已载入；请保存一次以添加任务编号" : "配置已载入");
    updateCount();
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function saveConfig() {
  try {
    const payload = {friends: collectTasks(), daily_note: {
      enabled: dailyNoteEnabled.checked,
      city: dailyNoteCity.value.trim(),
    }, email: {
      enabled: emailEnabled.checked,
      qq_address: qqAddress.value.trim(),
      recipient: emailRecipient.value.trim(),
      authorization_code: authorizationCode.value.trim(),
    }};
    saveButton.disabled = true;
    setStatus("正在保存…");
    const response = await fetch("/api/config", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "保存失败");
    dirty = false;
    setStatus(`已保存 ${data.count} 条任务到 config.json`);
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    saveButton.disabled = false;
  }
}

document.querySelector("#add-task").addEventListener("click", () => {
  const card = addTask();
  dirty = true;
  setStatus("有未保存的更改");
  card.querySelector(".name").focus();
});
saveButton.addEventListener("click", saveConfig);
document.querySelectorAll(".email-card").forEach(card => {
  card.addEventListener("input", () => {
    dirty = true;
    setStatus("有未保存的更改");
  });
  card.addEventListener("change", () => {
    dirty = true;
    setStatus("有未保存的更改");
  });
});
window.addEventListener("beforeunload", event => {
  if (!dirty) return;
  event.preventDefault();
  event.returnValue = "";
});
loadConfig();
