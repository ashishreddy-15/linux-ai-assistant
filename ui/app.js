/**
 * Linux AI Assistant - Frontend Client Application
 * Bridges UI interactions with Python backend via window.pywebview.api
 */

let appState = {
  profile: {},
  session: {},
  providers: {},
  activeProvider: "groq",
  currentModel: "",
  skillLevel: "Intermediate",
  theme: "dark",
  activeAssistantBubble: null,
  activePendingApprovalId: null,
  terminalLineCount: 0,
  isTerminalOpen: false
};

// --- ANSI Escape Code to HTML Converter ---
function ansiToHtml(text) {
  if (!text) return "";
  const ansiMap = {
    "30": "#71717a", "31": "#ef4444", "32": "#10b981", "33": "#f59e0b",
    "34": "#3b82f6", "35": "#a855f7", "36": "#06b6d4", "37": "#f4f4f5",
    "90": "#a1a1aa", "91": "#f87171", "92": "#34d399", "93": "#fbbf24",
    "94": "#60a5fa", "95": "#c084fc", "96": "#22d3ee", "97": "#ffffff"
  };

  let clean = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return clean.replace(/\x1b\[([0-9;]+)m/g, (match, codes) => {
    const codeList = codes.split(";");
    for (const code of codeList) {
      if (ansiMap[code]) {
        return `<span style="color: ${ansiMap[code]}; font-weight: 500;">`;
      }
      if (code === "0" || code === "39") {
        return "</span>";
      }
    }
    return "";
  });
}

// --- Basic Markdown Formatter ---
function formatMarkdown(text) {
  if (!text) return "";
  let escaped = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  // Code blocks: ```bash ... ```
  escaped = escaped.replace(/```([a-zA-Z0-9_\-]+)?\n([\s\S]*?)```/g, (match, lang, code) => {
    return `<pre><code class="language-${lang || 'text'}">${code.trim()}</code></pre>`;
  });

  // Inline code: `cmd`
  escaped = escaped.replace(/`([^`]+)`/g, '<code>$1</code>');

  // Bold: **text**
  escaped = escaped.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

  // Line breaks to <br> outside <pre>
  const parts = escaped.split(/(<pre>[\s\S]*?<\/pre>)/g);
  for (let i = 0; i < parts.length; i++) {
    if (!parts[i].startsWith("<pre>")) {
      parts[i] = parts[i].replace(/\n/g, "<br>");
    }
  }
  return parts.join("");
}

// --- UI Elements ---
const badgeSystem = document.getElementById("badge-system");
const badgeSystemText = document.getElementById("badge-system-text");
const badgeProviderText = document.getElementById("badge-provider-text");
const statusIndicator = document.getElementById("status-indicator");
const messagesStream = document.getElementById("messages-stream");
const chatScrollArea = document.getElementById("chat-scroll-area");
const promptInput = document.getElementById("prompt-input");
const btnSend = document.getElementById("btn-send");
const btnStop = document.getElementById("btn-stop");
const starterPanel = document.getElementById("starter-panel");

// Terminal Drawer Elements
const terminalDrawer = document.getElementById("terminal-drawer");
const terminalHeader = document.getElementById("terminal-header");
const terminalContent = document.getElementById("terminal-content");
const terminalCount = document.getElementById("terminal-count");
const btnToggleTerminal = document.getElementById("btn-toggle-terminal");
const btnClearTerminal = document.getElementById("btn-clear-terminal");

// Theme Switcher Elements
const btnThemeToggle = document.getElementById("btn-theme-toggle");
const themeIconSun = document.getElementById("theme-icon-sun");
const themeIconMoon = document.getElementById("theme-icon-moon");
const themeBtnLabel = document.getElementById("theme-btn-label");
const selectTheme = document.getElementById("select-theme");

// Settings Modal Elements
const settingsModal = document.getElementById("settings-modal");
const btnSettings = document.getElementById("btn-settings");
const btnCloseModal = document.getElementById("btn-close-modal");
const btnCancelSettings = document.getElementById("btn-cancel-settings");
const btnSaveSettings = document.getElementById("btn-save-settings");
const selectSkill = document.getElementById("select-skill");
const selectProvider = document.getElementById("select-provider");
const selectModel = document.getElementById("select-model");
const inputApiKey = document.getElementById("input-api-key");
const btnTestKey = document.getElementById("btn-test-key");
const testKeyResult = document.getElementById("test-key-result");
const keyValidationBadge = document.getElementById("key-validation-badge");
const systemDetailsBox = document.getElementById("system-details-box");
const btnRefreshSystem = document.getElementById("btn-refresh-system");
const btnOpenPortal = document.getElementById("btn-open-portal");
const providerGuidance = document.getElementById("provider-guidance");

// --- Initialization ---
async function initApp() {
  if (!window.pywebview || !window.pywebview.api) {
    setTimeout(initApp, 50);
    return;
  }

  try {
    const initialState = await window.pywebview.api.get_initial_state();
    appState.profile = initialState.profile || {};
    appState.session = initialState.session || {};
    appState.providers = initialState.providers || {};
    appState.activeProvider = initialState.active_provider || "groq";
    appState.currentModel = initialState.current_model || "";
    appState.skillLevel = initialState.skill_level || "Intermediate";
    appState.theme = initialState.theme || appState.profile.theme || "dark";

    applyTheme(appState.theme);
    updateHeaderBadges();
    populateSettingsModal();

    // If no API key configured and provider is not local, open settings modal on first run
    if (!initialState.has_active_key && appState.activeProvider !== "ollama") {
      openSettingsModal();
    }
  } catch (err) {
    console.error("Initialization error:", err);
  }
}

function updateHeaderBadges() {
  const p = appState.profile;
  const pm = p.primary_package_manager || "unknown";
  const os = p.os_pretty_name || p.os_name || "Linux";
  const arch = p.cpu_architecture || "x86_64";
  badgeSystemText.textContent = `${os} • ${pm} • ${arch}`;

  const prov = appState.activeProvider;
  const model = appState.currentModel || (appState.providers[prov] ? appState.providers[prov].default_model : "");
  badgeProviderText.textContent = `${prov} • ${model}`;
}

// --- Theme Management ---
function applyTheme(theme) {
  const root = document.documentElement;
  let effective = theme;
  if (theme === "system") {
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    effective = prefersDark ? "dark" : "light";
  }

  if (effective === "light") {
    root.setAttribute("data-theme", "light");
    if (themeIconSun) themeIconSun.style.display = "block";
    if (themeIconMoon) themeIconMoon.style.display = "none";
    if (themeBtnLabel) themeBtnLabel.textContent = "Light";
  } else {
    root.removeAttribute("data-theme");
    if (themeIconSun) themeIconSun.style.display = "none";
    if (themeIconMoon) themeIconMoon.style.display = "block";
    if (themeBtnLabel) themeBtnLabel.textContent = "Dark";
  }

  if (selectTheme) {
    selectTheme.value = theme;
  }
}

async function toggleTheme() {
  const current = appState.theme === "light" ? "dark" : "light";
  appState.theme = current;
  applyTheme(current);
  if (window.pywebview && window.pywebview.api && window.pywebview.api.set_theme) {
    try {
      await window.pywebview.api.set_theme(current);
    } catch (err) {
      console.warn("Failed to persist theme via bridge:", err);
    }
  }
}

// --- Event Dispatcher (Invoked from Python Bridge) ---
window.__onAssistantEvent = function(eventName, data) {
  switch (eventName) {
    case "response_chunk":
      handleResponseChunk(data);
      break;
    case "response_ready":
      handleResponseReady(data);
      break;
    case "approval_requested":
      handleApprovalRequested(data);
      break;
    case "command_output":
      appendTerminalOutput(data);
      break;
    case "command_finished":
      handleCommandFinished(data);
      break;
    case "state_changed":
      handleStateChanged(data);
      break;
    case "error_occurred":
      handleErrorOccurred(data);
      break;
    default:
      console.log("Unhandled event:", eventName, data);
  }
};

// --- Chat & Bubble Handlers ---
function addUserMessage(text) {
  const row = document.createElement("div");
  row.className = "message-row user";
  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.textContent = text;
  row.appendChild(bubble);
  messagesStream.appendChild(row);
  scrollToBottom();
}

function createAssistantBubble() {
  const row = document.createElement("div");
  row.className = "message-row assistant";
  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.innerHTML = '<span style="opacity: 0.6;">Thinking...</span>';
  row.appendChild(bubble);
  messagesStream.appendChild(row);
  scrollToBottom();
  appState.activeAssistantBubble = bubble;
  return bubble;
}

function handleResponseChunk(chunk) {
  if (!appState.activeAssistantBubble) {
    createAssistantBubble();
  }
  if (appState.activeAssistantBubble.getAttribute("data-raw") === null) {
    appState.activeAssistantBubble.setAttribute("data-raw", "");
  }
  const current = appState.activeAssistantBubble.getAttribute("data-raw") + chunk;
  appState.activeAssistantBubble.setAttribute("data-raw", current);
  appState.activeAssistantBubble.innerHTML = formatMarkdown(current);
  scrollToBottom();
}

function handleResponseReady(fullText) {
  if (!appState.activeAssistantBubble) {
    createAssistantBubble();
  }
  appState.activeAssistantBubble.innerHTML = formatMarkdown(fullText);
  appState.activeAssistantBubble.removeAttribute("data-raw");
  appState.activeAssistantBubble = null;
  scrollToBottom();
}

function handleApprovalRequested(payload) {
  // Hide thinking placeholder if active
  if (appState.activeAssistantBubble && !appState.activeAssistantBubble.getAttribute("data-raw")) {
    appState.activeAssistantBubble.parentElement.remove();
    appState.activeAssistantBubble = null;
  }

  const card = document.createElement("div");
  const isHighRisk = payload.high_risk || false;
  card.className = `approval-card ${isHighRisk ? "high-risk" : ""}`;

  const isRoot = payload.requires_root;
  let riskBadgeHtml = '<span class="risk-badge normal">Safe Action</span>';
  if (isHighRisk) {
    riskBadgeHtml = '<span class="risk-badge danger">⚠️ High Risk</span>';
  } else if (isRoot) {
    riskBadgeHtml = '<span class="risk-badge root">🔑 Root Required</span>';
  }

  const command = payload.command || "";
  const explanation = payload.explanation || "The assistant proposes running the following command to proceed:";
  const skill = appState.skillLevel || "Intermediate";

  let commandSectionHtml = "";
  if (skill === "Beginner") {
    commandSectionHtml = `
      <details style="font-size: 12px; color: var(--text-dim); cursor: pointer;">
        <summary style="outline: none; margin-bottom: 6px;">Show proposed shell command ▸</summary>
        <div class="command-box" id="card-cmd-display">${command}</div>
      </details>
    `;
  } else if (skill === "Advanced") {
    commandSectionHtml = `
      <div style="font-size: 12px; color: var(--text-muted); margin-bottom: 4px;">Edit proposed command:</div>
      <input type="text" class="command-input-edit" id="card-cmd-edit" value="${command}" />
    `;
  } else {
    commandSectionHtml = `<div class="command-box" id="card-cmd-display">${command}</div>`;
  }

  card.innerHTML = `
    <div class="approval-header">
      <div class="approval-title">
        <span>Proposed Action</span>
        ${riskBadgeHtml}
      </div>
    </div>
    <div class="approval-explanation">${explanation}</div>
    ${commandSectionHtml}
    <div class="approval-actions">
      <button class="btn-secondary" id="btn-reject-action">Reject</button>
      <button class="btn-primary" id="btn-approve-action">Approve & Run</button>
    </div>
  `;

  messagesStream.appendChild(card);
  scrollToBottom();

  // Wire buttons
  const btnApprove = card.querySelector("#btn-approve-action");
  const btnReject = card.querySelector("#btn-reject-action");

  btnApprove.addEventListener("click", () => {
    let finalCommand = command;
    const editInput = card.querySelector("#card-cmd-edit");
    if (editInput) {
      finalCommand = editInput.value.trim() || command;
    }
    btnApprove.disabled = true;
    btnReject.disabled = true;
    btnApprove.textContent = "Executing...";
    window.pywebview.api.approve_command(finalCommand);
    openTerminalDrawer();
  });

  btnReject.addEventListener("click", () => {
    btnApprove.disabled = true;
    btnReject.disabled = true;
    card.style.opacity = "0.5";
    window.pywebview.api.reject_command("User declined proposed action.");
  });
}

function handleErrorOccurred(errorMessage) {
  if (appState.activeAssistantBubble) {
    appState.activeAssistantBubble.innerHTML = `<span style="color: var(--red-main);">⚠️ ${errorMessage}</span>`;
    appState.activeAssistantBubble = null;
  } else {
    const row = document.createElement("div");
    row.className = "message-row assistant";
    row.innerHTML = `<div class="message-bubble" style="color: var(--red-main);">⚠️ ${errorMessage}</div>`;
    messagesStream.appendChild(row);
  }
  scrollToBottom();
}

function handleStateChanged(state) {
  statusIndicator.className = "status-dot";
  if (state === "THINKING") {
    statusIndicator.classList.add("thinking");
    btnStop.style.display = "block";
  } else if (state === "EXECUTING") {
    statusIndicator.classList.add("executing");
    btnStop.style.display = "block";
  } else if (state === "ERROR") {
    statusIndicator.classList.add("error");
    btnStop.style.display = "none";
  } else {
    btnStop.style.display = "none";
  }
}

// --- Terminal Output Streaming ---
function appendTerminalOutput(chunk) {
  if (terminalContent.textContent === "Waiting for command execution...") {
    terminalContent.innerHTML = "";
  }
  terminalContent.innerHTML += ansiToHtml(chunk);
  terminalContent.scrollTop = terminalContent.scrollHeight;

  appState.terminalLineCount += (chunk.match(/\n/g) || []).length;
  terminalCount.textContent = `(${appState.terminalLineCount} lines)`;
}

function handleCommandFinished(result) {
  const code = result.exit_code;
  const tag = code === 0 ? "SUCCESS" : `FAILED (Exit code ${code})`;
  const color = code === 0 ? "var(--emerald-main)" : "var(--red-main)";
  appendTerminalOutput(`\n[Process completed: ${tag}]\n`);
}

function openTerminalDrawer() {
  terminalDrawer.classList.add("open");
  btnToggleTerminal.textContent = "▼ Collapse";
  appState.isTerminalOpen = true;
}

function toggleTerminalDrawer() {
  if (appState.isTerminalOpen) {
    terminalDrawer.classList.remove("open");
    btnToggleTerminal.textContent = "▲ Expand";
    appState.isTerminalOpen = false;
  } else {
    openTerminalDrawer();
  }
}

function clearTerminal() {
  terminalContent.innerHTML = "";
  appState.terminalLineCount = 0;
  terminalCount.textContent = "(0 lines)";
}

// --- Prompt Submission ---
function submitPrompt() {
  const text = promptInput.value.trim();
  if (!text) return;

  starterPanel.style.display = "none";
  addUserMessage(text);
  promptInput.value = "";
  createAssistantBubble();

  window.pywebview.api.submit_prompt(text);
}

// --- Settings & Modal Logic ---
function openSettingsModal() {
  settingsModal.classList.add("open");
}

function closeSettingsModal() {
  settingsModal.classList.remove("open");
}

function populateSettingsModal() {
  const p = appState.profile;
  const s = appState.session;

  const osPretty = p.os_pretty_name || p.os_name || "Linux";
  const osVer = p.os_version || "";
  const displayOs = osVer && !osPretty.includes(osVer) ? `${osPretty} (${osVer})` : osPretty;

  systemDetailsBox.textContent = [
    `OS:       ${displayOs}`,
    `Package:  Primary: ${p.primary_package_manager || "unknown"} | Helpers: ${(p.secondary_helpers || []).join(", ") || "none"}`,
    `Hardware: Arch: ${p.cpu_architecture || "x86_64"} | RAM: ${p.total_ram_gb ? p.total_ram_gb + " GB" : "Unknown"}`,
    `Storage:  Free: ${s.home_disk_free_gb ? s.home_disk_free_gb + " GB" : "Unknown"} / Total: ${s.home_disk_total_gb ? s.home_disk_total_gb + " GB" : ""}`,
    `Session:  Desktop: ${s.xdg_current_desktop || "Wayland"} (${s.xdg_session_type || ""})`
  ].join("\n");

  selectSkill.value = appState.skillLevel;
  if (selectTheme) {
    selectTheme.value = appState.theme || "dark";
  }
  selectProvider.value = appState.activeProvider;
  updateProviderDropdownUI();
}

function updateProviderDropdownUI() {
  const prov = selectProvider.value;
  const cfg = appState.providers[prov] || {};

  // Update models dropdown
  selectModel.innerHTML = "";
  const models = cfg.models || [];
  models.forEach(m => {
    const opt = document.createElement("option");
    opt.value = m;
    opt.textContent = m;
    if (m === appState.currentModel || m === cfg.default_model) {
      opt.selected = true;
    }
    selectModel.appendChild(opt);
  });

  // Local Ollama handling
  const apiKeyGroup = document.getElementById("group-api-key");
  if (cfg.is_local) {
    apiKeyGroup.style.display = "none";
    providerGuidance.style.display = "block";
    document.getElementById("guidance-title").textContent = "Local Ollama Daemon";
    document.getElementById("guidance-text").textContent = "Ensure your local Ollama daemon is running on http://127.0.0.1:11434.";
    btnOpenPortal.style.display = "none";
  } else {
    apiKeyGroup.style.display = "flex";
    providerGuidance.style.display = "block";
    document.getElementById("guidance-title").textContent = `Free ${cfg.display_name || prov} API Key`;
    document.getElementById("guidance-text").textContent = `Click below to create your free ${cfg.display_name} API key.`;
    btnOpenPortal.style.display = "block";
    inputApiKey.placeholder = cfg.key_prefix ? `Starts with ${cfg.key_prefix}...` : "Paste API key...";
  }

  testKeyResult.style.display = "none";
  keyValidationBadge.style.display = "none";
}

async function testCurrentKey() {
  const prov = selectProvider.value;
  const key = inputApiKey.value.trim();

  btnTestKey.disabled = true;
  btnTestKey.textContent = "Testing...";
  testKeyResult.style.display = "block";
  testKeyResult.textContent = "Verifying credentials with provider API...";
  testKeyResult.style.color = "var(--text-muted)";

  try {
    let res;
    if (prov === "ollama") {
      res = await window.pywebview.api.check_ollama();
      if (res.running) {
        testKeyResult.textContent = "✓ Connected to local Ollama daemon!";
        testKeyResult.style.color = "var(--emerald-main)";
        populateModels(res.models);
      } else {
        testKeyResult.textContent = "✕ Ollama daemon not reachable at 127.0.0.1:11434";
        testKeyResult.style.color = "var(--red-main)";
      }
    } else {
      res = await window.pywebview.api.test_key(prov, key);
      if (res.valid) {
        testKeyResult.textContent = `✓ ${res.message}`;
        testKeyResult.style.color = "var(--emerald-main)";
        keyValidationBadge.style.display = "inline";
        if (res.models && res.models.length > 0) {
          populateModels(res.models);
        }
      } else {
        testKeyResult.textContent = `✕ ${res.message}`;
        testKeyResult.style.color = "var(--red-main)";
        keyValidationBadge.style.display = "none";
      }
    }
  } catch (err) {
    testKeyResult.textContent = `Test error: ${err}`;
    testKeyResult.style.color = "var(--red-main)";
  } finally {
    btnTestKey.disabled = false;
    btnTestKey.textContent = prov === "ollama" ? "Test Connection" : "Test Key";
  }
}

function populateModels(models) {
  if (!models || models.length === 0) return;
  selectModel.innerHTML = "";
  models.forEach(m => {
    const opt = document.createElement("option");
    opt.value = m;
    opt.textContent = m;
    selectModel.appendChild(opt);
  });
}

async function saveSettings() {
  const skill = selectSkill.value;
  const theme = selectTheme ? selectTheme.value : appState.theme;
  const prov = selectProvider.value;
  const model = selectModel.value;
  const key = inputApiKey.value.trim();

  btnSaveSettings.disabled = true;
  btnSaveSettings.textContent = "Saving...";

  try {
    await window.pywebview.api.save_settings(skill, prov, model, key, theme);
    appState.skillLevel = skill;
    appState.theme = theme;
    appState.activeProvider = prov;
    appState.currentModel = model;

    applyTheme(theme);
    updateHeaderBadges();
    closeSettingsModal();
  } catch (err) {
    alert("Failed to save settings: " + err);
  } finally {
    btnSaveSettings.disabled = false;
    btnSaveSettings.textContent = "Save & Continue";
  }
}

async function refreshSystemInfo() {
  if (btnRefreshSystem) {
    btnRefreshSystem.disabled = true;
    btnRefreshSystem.innerHTML = "<span>Syncing...</span>";
  }

  try {
    const res = await window.pywebview.api.refresh_system_profile();
    if (res && res.profile) {
      appState.profile = res.profile;
      appState.session = res.session || {};
      updateHeaderBadges();
      populateSettingsModal();
      if (btnRefreshSystem) {
        btnRefreshSystem.innerHTML = "<span>✓ Synced!</span>";
        setTimeout(() => {
          btnRefreshSystem.disabled = false;
          btnRefreshSystem.innerHTML = `
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/>
            </svg>
            <span>Sync Info</span>
          `;
        }, 1500);
      }
    }
  } catch (err) {
    console.error("Failed to refresh system info:", err);
    if (btnRefreshSystem) {
      btnRefreshSystem.disabled = false;
      btnRefreshSystem.innerHTML = "<span>Sync Info</span>";
    }
  }
}

function scrollToBottom() {
  chatScrollArea.scrollTop = chatScrollArea.scrollHeight;
}

// --- Event Listeners ---
btnSend.addEventListener("click", submitPrompt);
promptInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    submitPrompt();
  }
});

btnStop.addEventListener("click", () => {
  window.pywebview.api.abort_execution();
});

terminalHeader.addEventListener("click", toggleTerminalDrawer);
btnClearTerminal.addEventListener("click", (e) => {
  e.stopPropagation();
  clearTerminal();
});
btnToggleTerminal.addEventListener("click", (e) => {
  e.stopPropagation();
  toggleTerminalDrawer();
});

btnSettings.addEventListener("click", openSettingsModal);
if (btnThemeToggle) {
  btnThemeToggle.addEventListener("click", toggleTheme);
}
if (selectTheme) {
  selectTheme.addEventListener("change", (e) => {
    applyTheme(e.target.value);
  });
}
if (btnRefreshSystem) {
  btnRefreshSystem.addEventListener("click", refreshSystemInfo);
}
if (badgeSystem) {
  badgeSystem.addEventListener("click", refreshSystemInfo);
}
btnCloseModal.addEventListener("click", closeSettingsModal);
btnCancelSettings.addEventListener("click", closeSettingsModal);
btnSaveSettings.addEventListener("click", saveSettings);

selectProvider.addEventListener("change", updateProviderDropdownUI);
btnTestKey.addEventListener("click", testCurrentKey);
btnOpenPortal.addEventListener("click", () => {
  const prov = selectProvider.value;
  const cfg = appState.providers[prov] || {};
  if (cfg.portal_url) {
    window.pywebview.api.open_external_url(cfg.portal_url);
  }
});

// Starter chips click
document.querySelectorAll(".chip-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    const prompt = btn.getAttribute("data-prompt");
    if (prompt) {
      promptInput.value = prompt;
      submitPrompt();
    }
  });
});

// Bootstrap
window.addEventListener("pywebviewready", initApp);
window.addEventListener("DOMContentLoaded", () => {
  setTimeout(initApp, 200);
});
