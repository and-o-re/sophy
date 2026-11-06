// Frontend Desacoplado 100% Offline (Tema Ruínas Antigas & Floresta Pixel 16-Bit)

let currentBrowserTarget = "vault"; // 'vault' ou 'code'
let currentBrowserPath = "~";
let currentParentPath = "~";
let strictOfflineState = false;

// =====================================================================
// NAVEGAÇÃO ENTRE ABAS
// =====================================================================
document.querySelectorAll(".nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));

    btn.classList.add("active");
    const targetId = btn.getAttribute("data-tab");
    document.getElementById(targetId).classList.add("active");
    document.getElementById("page-title").textContent = btn.textContent.trim();
  });
});

// Relógio em Tempo Real no Topo
function updateClock() {
  const now = new Date();
  const formatted = now.toLocaleString("pt-BR", {
    weekday: "long",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  document.getElementById("realtime-clock").textContent = `🌿 Relógio do Santuário: ${formatted}`;
}
setInterval(updateClock, 1000);
updateClock();

// =====================================================================
// TELEMETRIA EM TEMPO REAL (VRAM RTX 4050, RAM, OLLAMA, NUVEM HÍBRIDA, SQLITE)
// =====================================================================
async function fetchTelemetry() {
  try {
    const res = await fetch("/api/telemetry");
    const data = await res.json();

    // Atualiza estado do Selo de Isolamento Offline
    applyOfflineVisualState(data.strict_offline_mode);

    // GPU RTX 4050
    const gpu = data.gpu;
    document.getElementById("gpu-temp").textContent = `${gpu.temperature_c}°C | ${gpu.power_draw_w}W`;
    document.getElementById("vram-numbers").textContent = `${gpu.vram_used_mb} / ${gpu.vram_total_mb} MiB (${gpu.vram_percent}%)`;
    const vramBar = document.getElementById("vram-bar");
    vramBar.style.width = `${Math.min(100, gpu.vram_percent)}%`;
    if (gpu.vram_percent > 85) {
      vramBar.classList.add("progress-warn");
    } else {
      vramBar.classList.remove("progress-warn");
    }
    document.getElementById("vram-details").textContent =
      `VRAM Ollama: ${gpu.ollama_vram_mb} MiB | Livre: ${gpu.vram_free_mb} MiB | Uso GPU: ${gpu.gpu_utilization_pct}%`;

    // RAM e SSD
    const ram = data.ram;
    document.getElementById("ram-pct").textContent = `${ram.percent}%`;
    document.getElementById("ram-numbers").textContent = `${ram.used_gb} / ${ram.total_gb} GB`;
    document.getElementById("ram-bar").style.width = `${Math.min(100, ram.percent)}%`;
    document.getElementById("disk-details").textContent =
      `SSD Livre: ${data.disk.free_gb} GB de ${data.disk.total_gb} GB (${data.disk.percent}% ocupado)`;

    // Status Nuvem Híbrida (OpenAI Code + Google AI Studio)
    const badgeCloud = document.getElementById("badge-cloud-ai");
    if (badgeCloud && data.cloud_ai) {
      const hasOai = data.cloud_ai.openai_code_configured;
      const hasGem = data.cloud_ai.google_ai_studio_configured;
      if (data.strict_offline_mode) {
        badgeCloud.textContent = "🔒 Nuvem Selada (Offline)";
      } else if (hasOai && hasGem) {
        badgeCloud.textContent = "✨ Híbrido: OpenAI Code + Gemini";
      } else if (hasOai || hasGem) {
        badgeCloud.textContent = `✨ Híbrido: ${hasOai ? "OpenAI Code" : "Gemini"}`;
      } else {
        badgeCloud.textContent = "🌿 Apenas Local";
      }
    }

    // Status Ollama
    const badgeOllama = document.getElementById("badge-ollama");
    if (data.ollama.online) {
      badgeOllama.textContent = `🟢 Ollama (${data.ollama.reasoning_model})`;
    } else {
      badgeOllama.textContent = "🌙 Ollama em Repouso";
    }

    const modelsList = document.getElementById("ollama-models-list");
    if (!data.ollama.loaded_models || data.ollama.loaded_models.length === 0) {
      modelsList.innerHTML = `<div class="muted">Nenhum modelo carregado na VRAM agora (repousando no santuário, keep_alive=5m).</div>`;
    } else {
      modelsList.innerHTML = data.ollama.loaded_models
        .map(
          (m) => `
          <div class="model-item">
            <div>
              <strong>${m.name}</strong>
              <div class="muted">Tamanho total: ${m.size_mb} MiB</div>
            </div>
            <span class="tag">VRAM: ${m.vram_mb} MiB (${m.gpu_offload_pct}% GPU)</span>
          </div>
        `
        )
        .join("");
    }

    // Contagens do Banco SQLite
    const dbGrid = document.getElementById("db-stats-grid");
    const counts = data.database_counts || {};
    dbGrid.innerHTML = Object.entries(counts)
      .map(
        ([table, count]) => `
        <div class="db-stat-box">
          <strong>${count}</strong>
          <span>${table}</span>
        </div>
      `
      )
      .join("");

    // Atualiza Cartões de Fans & RGB do Teclado
    if (data.hardware_controls) {
      updateHardwareControlsUI(data.hardware_controls);
    }
  } catch (err) {
    console.error("Erro ao buscar telemetria:", err);
  }
}

document.getElementById("btn-refresh-telemetry").addEventListener("click", fetchTelemetry);
setInterval(fetchTelemetry, 3000);

// =====================================================================
// CARREGAMENTO DE CONFIGURAÇÃO INICIAL
// =====================================================================
async function loadConfig() {
  const res = await fetch("/api/config");
  const cfg = await res.json();

  applyOfflineVisualState(cfg.strict_offline_mode);
  document.getElementById("input-vault-path").value = cfg.obsidian_vault_path || "";
  document.getElementById("input-code-dirs").value = cfg.allowed_code_dirs || "";
  document.getElementById("textarea-persona").value = cfg.persona_markdown || "";

  const cred = cfg.credentials_status || {};
  const oaiBadge = document.getElementById("openai-code-badge");
  if (oaiBadge) {
    oaiBadge.textContent = cred.openai_code_configured
      ? `Ativa (${cred.openai_code_masked})`
      : "Ausente";
  }
  const gemBadge = document.getElementById("google-ai-badge");
  if (gemBadge) {
    gemBadge.textContent = cred.google_ai_studio_configured
      ? `Ativa (${cred.google_ai_studio_masked})`
      : "Ausente";
  }
  const antBadge = document.getElementById("anthropic-badge");
  if (antBadge) {
    antBadge.textContent = cred.anthropic_configured
      ? `Ativa (${cred.anthropic_masked})`
      : "Opcional";
  }
  document.getElementById("discord-badge").textContent = cred.discord_configured
    ? `Ativo (${cred.discord_token_masked})`
    : "Não configurado";
  document.getElementById("input-discord-guild").value = cred.discord_guild_id || "";
  const chInput = document.getElementById("input-discord-channel");
  if (chInput) {
    chInput.value = cred.discord_channel_id || "";
  }
  document.getElementById("google-badge").textContent = cred.google_configured
    ? "Configurado"
    : "Pendente OAuth";
  document.getElementById("input-google-id").value = cred.google_client_id || "";
  document.getElementById("input-wpp-port").value = cred.whatsapp_bridge_port || "3001";

  const compBadge = document.getElementById("composio-badge");
  if (compBadge) {
    compBadge.textContent = cred.composio_configured ? `Ativa (${cred.composio_masked})` : "Opcional";
  }
  const finBadge = document.getElementById("finance-badge");
  if (finBadge) {
    const anyFin = cred.finance_fintel_configured || cred.finance_adanos_configured || cred.alpha_vantage_configured;
    finBadge.textContent = anyFin ? "yfinance + Pro Ativo" : "yfinance & Hyperliquid Ativos";
  }
  const spotBadge = document.getElementById("spotify-badge");
  if (spotBadge) {
    spotBadge.textContent = cred.spotipy_configured ? "Configurado" : "Opcional";
  }
  const spotIdInput = document.getElementById("input-spotify-id");
  if (spotIdInput) {
    spotIdInput.value = cred.spotipy_client_id || "";
  }
  const gwsStatus = document.getElementById("gws-status-text");
  if (gwsStatus && cred.google_workspace_status) {
    const gs = cred.google_workspace_status;
    if (gs.oauth_token_exists) {
      gwsStatus.innerHTML = `🟢 <strong>Google Workspace Autenticado!</strong> (<code>${gs.client_secret_path}</code>)`;
    } else if (gs.client_secret_exists) {
      gwsStatus.innerHTML = `🟡 <code>client_secret.json</code> pronto! Execute no terminal: <code>${gs.auth_command}</code>`;
    }
  }
}

// =====================================================================
// SELO DO ERMITÃO: MODO ESTRITAMENTE OFFLINE
// =====================================================================
function applyOfflineVisualState(isStrictOffline) {
  strictOfflineState = Boolean(isStrictOffline);
  const box = document.getElementById("emergency-box");
  const label = document.getElementById("offline-label");
  const btn = document.getElementById("btn-emergency-offline");

  if (strictOfflineState) {
    box.classList.add("strict-active");
    label.textContent = "[SELO] MODO ISOLADO NAS RUÍNAS";
    btn.textContent = "[🌿] REABRIR PONTES HÍBRIDAS";
  } else {
    box.classList.remove("strict-active");
    label.textContent = "[❖] SANTUÁRIO HÍBRIDO ATIVO";
    btn.textContent = "[❖] ATIVAR SELO OFFLINE";
  }
}

document.getElementById("btn-emergency-offline").addEventListener("click", async () => {
  const nextState = !strictOfflineState;
  const res = await fetch("/api/offline-mode", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ strict_offline_mode: nextState }),
  });
  const data = await res.json();
  applyOfflineVisualState(data.strict_offline_mode);
  fetchTelemetry();
});

// =====================================================================
// SELETOR VISUAL DE PASTAS E SALVAMENTO DE DIRETÓRIOS
// =====================================================================
async function openFolderBrowser(targetType, initialPath = "~") {
  currentBrowserTarget = targetType;
  const modal = document.getElementById("folder-browser-modal");
  modal.classList.remove("hidden");
  await navigateBrowser(initialPath);
}

async function navigateBrowser(pathStr) {
  const res = await fetch(`/api/browse-folders?base_path=${encodeURIComponent(pathStr)}`);
  const data = await res.json();
  currentBrowserPath = data.current_path;
  currentParentPath = data.parent_path;

  document.getElementById("browser-current-path").textContent = currentBrowserPath;
  const listEl = document.getElementById("browser-folder-list");
  listEl.innerHTML = data.folders
    .map(
      (f) =>
        `<div class="folder-item" data-path="${f.path}">🌿 ${f.name}</div>`
    )
    .join("");

  listEl.querySelectorAll(".folder-item").forEach((item) => {
    item.addEventListener("click", () => navigateBrowser(item.getAttribute("data-path")));
  });
}

document.getElementById("btn-browse-vault").addEventListener("click", () => {
  openFolderBrowser("vault", document.getElementById("input-vault-path").value || "~");
});

document.getElementById("btn-browse-code").addEventListener("click", () => {
  openFolderBrowser("code", "~");
});

document.getElementById("btn-browser-up").addEventListener("click", () => {
  navigateBrowser(currentParentPath);
});

document.getElementById("btn-browser-close").addEventListener("click", () => {
  document.getElementById("folder-browser-modal").classList.add("hidden");
});

document.getElementById("btn-browser-select").addEventListener("click", () => {
  if (currentBrowserTarget === "vault") {
    document.getElementById("input-vault-path").value = currentBrowserPath;
  } else {
    const input = document.getElementById("input-code-dirs");
    const existing = input.value
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (!existing.includes(currentBrowserPath)) {
      existing.push(currentBrowserPath);
    }
    input.value = existing.join(",");
  }
  document.getElementById("folder-browser-modal").classList.add("hidden");
});

document.getElementById("btn-save-directories").addEventListener("click", async () => {
  const vaultPath = document.getElementById("input-vault-path").value.trim();
  const codeDirs = document.getElementById("input-code-dirs").value.trim();
  const feedback = document.getElementById("dir-save-feedback");

  const res = await fetch("/api/directories", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      obsidian_vault_path: vaultPath,
      allowed_code_dirs: codeDirs,
    }),
  });
  if (res.ok) {
    feedback.textContent = "🌿 Territórios gravados na pedra!";
    setTimeout(() => (feedback.textContent = ""), 3500);
  }
});

// =====================================================================
// CREDENCIAIS E RENDERIZADOR DE RUNA QR CODE EM CANVAS
// =====================================================================
document.getElementById("btn-save-credentials").addEventListener("click", async () => {
  const getVal = (id) => {
    const el = document.getElementById(id);
    return el ? el.value : "";
  };
  const payload = {
    openai_code_api_key: getVal("input-openai-code-key"),
    google_ai_studio_api_key: getVal("input-google-ai-key"),
    anthropic_api_key: getVal("input-anthropic-key"),
    discord_bot_token: getVal("input-discord-token"),
    discord_guild_id: getVal("input-discord-guild"),
    discord_channel_id: getVal("input-discord-channel"),
    google_client_id: getVal("input-google-id"),
    google_client_secret: getVal("input-google-secret"),
    whatsapp_bridge_port: getVal("input-wpp-port"),
    composio_api_key: getVal("input-composio-key"),
    finance_fintel_api_key: getVal("input-fintel-key"),
    finance_adanos_api_key: getVal("input-adanos-key"),
    alpha_vantage_api_key: getVal("input-alphavantage-key"),
    spotipy_client_id: getVal("input-spotify-id"),
    spotipy_client_secret: getVal("input-spotify-secret"),
  };
  const res = await fetch("/api/credentials", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (res.ok) {
    [
      "input-openai-code-key",
      "input-google-ai-key",
      "input-anthropic-key",
      "input-discord-token",
      "input-google-secret",
      "input-composio-key",
      "input-fintel-key",
      "input-adanos-key",
      "input-alphavantage-key",
      "input-spotify-secret",
    ].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.value = "";
    });
    const fb = document.getElementById("cred-save-feedback");
    fb.textContent = "✨ Relíquias e chaves guardadas no .env!";
    loadConfig();
    fetchTelemetry();
    setTimeout(() => (fb.textContent = ""), 3500);
  }
});

function drawMatrixQR(canvas, seedText) {
  const ctx = canvas.getContext("2d");
  const size = 21;
  const cell = Math.floor(canvas.width / size);
  ctx.fillStyle = "#0c140e";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  function drawFinder(ox, oy) {
    ctx.fillStyle = "#d8b86a";
    for (let y = 0; y < 7; y++) {
      for (let x = 0; x < 7; x++) {
        if (
          x === 0 || x === 6 || y === 0 || y === 6 ||
          (x >= 2 && x <= 4 && y >= 2 && y <= 4)
        ) {
          ctx.fillRect((ox + x) * cell, (oy + y) * cell, cell, cell);
        }
      }
    }
  }
  drawFinder(0, 0);
  drawFinder(size - 7, 0);
  drawFinder(0, size - 7);

  ctx.fillStyle = "#86cf90";
  let hash = 2166136261;
  for (let i = 0; i < seedText.length; i++) {
    hash ^= seedText.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }

  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const inFinder =
        (x < 8 && y < 8) ||
        (x >= size - 8 && y < 8) ||
        (x < 8 && y >= size - 8);
      if (inFinder) continue;
      hash ^= (x * 31 + y * 17) & 0xff;
      hash = Math.imul(hash, 16777619);
      if (Math.abs(hash) % 2 === 0) {
        ctx.fillRect(x * cell, y * cell, cell, cell);
      }
    }
  }
}

async function refreshRealWhatsAppQR() {
  const statusEl = document.getElementById("wpp-qr-status");
  const realImg = document.getElementById("qr-real-img");
  const qrCanvas = document.getElementById("qr-canvas");
  try {
    const res = await fetch("/api/whatsapp/qr");
    if (!res.ok) {
      const err = await res.json();
      statusEl.textContent = `⛔ ${err.detail}`;
      return;
    }
    const data = await res.json();
    if (data.paired) {
      if (realImg) realImg.style.display = "none";
      if (qrCanvas) {
        qrCanvas.style.display = "block";
        drawMatrixQR(qrCanvas, "PAIRED:" + (data.jid || "OK"));
      }
      statusEl.textContent = `🟢 WhatsApp Pareado e Conectado! JID: ${data.jid || "Ativo"} (Porta ${data.bridge_port})`;
    } else if (data.qr_image_data_url) {
      if (qrCanvas) qrCanvas.style.display = "none";
      if (realImg) {
        realImg.src = data.qr_image_data_url;
        realImg.style.display = "inline-block";
      }
      statusEl.textContent = `📱 ESCANEIE AGORA COM SEU WHATSAPP (Aparelhos Conectados -> Conectar um aparelho) | Porta ${data.bridge_port}`;
    } else {
      if (qrCanvas) qrCanvas.style.display = "none";
      statusEl.textContent = `⏳ Conectando ao servidor do WhatsApp (Porta ${data.bridge_port})... aguarde 2 segundos.`;
    }
  } catch (err) {
    console.error("Erro ao consultar QR WhatsApp:", err);
  }
}

document.getElementById("btn-generate-qr").addEventListener("click", () => {
  document.getElementById("wpp-qr-status").textContent = "⏳ Atualizando QR Code Real do WhatsApp...";
  refreshRealWhatsAppQR();
});

const gwsBtn = document.getElementById("btn-google-oauth");
if (gwsBtn) {
  gwsBtn.addEventListener("click", async () => {
    const gwsStatus = document.getElementById("gws-status-text");
    const res = await fetch("/api/google/auth-start", { method: "POST" });
    const data = await res.json();
    if (gwsStatus) {
      gwsStatus.textContent = data.message || "";
    }
    setTimeout(loadConfig, 5000);
  });
}

async function loadWorkersLogs() {
  try {
    const res = await fetch("/api/workers/logs");
    const data = await res.json();
    const listEl = document.getElementById("workers-logs-list");
    if (!data.logs || data.logs.length === 0) {
      listEl.innerHTML = `<div class="muted">Nenhuma expedição de sincronização registrada ainda.</div>`;
      return;
    }
    listEl.innerHTML = data.logs
      .slice(0, 6)
      .map(
        (l) => `
        <div class="model-item">
          <div>
            <strong>[${l.worker_name.toUpperCase()}]</strong> ${l.details}
            <div class="muted">Executado em: ${l.executed_at}</div>
          </div>
          <span class="tag">${l.status} (${l.items_synced})</span>
        </div>
      `
      )
      .join("");
  } catch (e) {
    console.error("Erro ao carregar logs dos workers:", e);
  }
}

document.getElementById("btn-run-sync-all").addEventListener("click", async () => {
  const btn = document.getElementById("btn-run-sync-all");
  btn.textContent = "[SINCRONIZANDO...]";
  await fetch("/api/workers/sync", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target: "all" }),
  });
  btn.textContent = "[SINCRONIZAR MENSAGEIROS AGORA]";
  await loadWorkersLogs();
  await fetchTelemetry();
});

// =====================================================================
// PERSONA E RE-INDEXAÇÃO RAG
// =====================================================================
document.getElementById("btn-save-persona").addEventListener("click", async () => {
  const content = document.getElementById("textarea-persona").value;
  const res = await fetch("/api/persona", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (res.ok) {
    const fb = document.getElementById("persona-save-feedback");
    fb.textContent = "🪄 Essência da Sophy (v2.1) selada no Grimório!";
    setTimeout(() => (fb.textContent = ""), 3500);
  }
});

document.getElementById("btn-reindex-rag").addEventListener("click", async () => {
  const btn = document.getElementById("btn-reindex-rag");
  btn.textContent = "[CATALOGANDO...]";
  const res = await fetch("/api/rag/reindex", { method: "POST" });
  const data = await res.json();
  btn.textContent = `🌿 ${data.chunks_indexed || 0} Pergaminhos`;
  setTimeout(() => (btn.textContent = "[CATALOGAR GRIMÓRIO RAG]"), 3000);
});

// =====================================================================
// ALTAR DE DIÁLOGO (CHAT COM A SOPHYA)
// =====================================================================
async function sendQuickChat() {
  const input = document.getElementById("input-chat-prompt");
  const prompt = input.value.trim();
  if (!prompt) return;

  const log = document.getElementById("chat-log");
  log.innerHTML += `<div class="chat-bubble user">${prompt}</div>`;
  input.value = "";
  log.scrollTop = log.scrollHeight;

  const res = await fetch("/api/agent/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt }),
  });
  const data = await res.json();
  const providerTag = data.provider ? ` [Fonte: ${data.provider}]` : "";
  const toolsSummary =
    data.tools_called && data.tools_called.length
      ? `\n\n[Ferramentas: ${data.tools_called.map((t) => t.tool).join(", ")}${providerTag}]`
      : providerTag
      ? `\n\n${providerTag}`
      : "";
  log.innerHTML += `<div class="chat-bubble assistant">${data.reply}${toolsSummary}</div>`;
  log.scrollTop = log.scrollHeight;
  fetchTelemetry();
}

document.getElementById("btn-send-chat").addEventListener("click", sendQuickChat);
document.getElementById("input-chat-prompt").addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendQuickChat();
});

// =====================================================================
// CONTROLES DE FANS & RGB DO TECLADO (AVELL STORM BS)
// =====================================================================
function updateHardwareControlsUI(hw) {
  const temps = hw.temperatures || {};
  const fans = hw.fans || {};
  const rgb = hw.keyboard_rgb || {};

  const fanBadge = document.getElementById("fan-mode-badge");
  if (fanBadge) {
    fanBadge.textContent = `${(fans.mode || "balanced").toUpperCase()} (${fans.speed_percent || 60}%)`;
  }

  const fanLine = document.getElementById("fan-status-line");
  if (fanLine) {
    const pctCpu = fans.cpu_fan_percent ?? "--";
    const pctGpu = fans.gpu_fan_percent ?? "--";
    const drvTag = hw.uniwill_driver_loaded ? "Driver TUXEDO Ativo" : "Driver TUXEDO Inativo";
    const guardTag = fans.safety_override ? " | ⚠ Manual cancelado pela temperatura" : "";
    fanLine.textContent =
      `CPU: ${temps.cpu_temp_c || "--"}°C | GPU: ${temps.gpu_temp_c || "--"}°C | Fans: ${pctCpu}% / ${pctGpu}% | [${drvTag}]${guardTag}`;
  }

  const drvBtn = document.getElementById("btn-activate-uniwill-driver");
  if (drvBtn) {
    if (hw.uniwill_driver_loaded) {
      drvBtn.textContent = "[✓ DRIVER AVELL (TUXEDO) ATIVO]";
      drvBtn.disabled = true;
    } else {
      drvBtn.textContent = "[⚡ ATIVAR DRIVER AVELL (TUXEDO)]";
      drvBtn.disabled = false;
    }
  }

  const rgbBadge = document.getElementById("rgb-status-badge");
  if (rgbBadge) {
    rgbBadge.textContent = `${rgb.color_hex || "#4d8a56"} (${rgb.brightness_percent ?? 80}% • ${rgb.effect || "static"})`;
  }
}

document.querySelectorAll(".fan-preset-btn").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const mode = btn.getAttribute("data-fan-mode");
    const fb = document.getElementById("fan-feedback");
    fb.textContent = "Ajustando ventos (pode levar até 15 s)...";
    const res = await fetch("/api/hardware/fans", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, speed_percent: 60 }),
    });
    const data = await res.json();
    fb.textContent = `🌀 ${data.fan_note || "Modo aplicado!"}`;
    fetchTelemetry();
    setTimeout(() => (fb.textContent = ""), 4500);
  });
});

const fanSlider = document.getElementById("slider-fan-speed");
if (fanSlider) {
  fanSlider.addEventListener("input", () => {
    document.getElementById("fan-slider-label").textContent = `${fanSlider.value}%`;
  });
}

document.getElementById("btn-apply-manual-fan").addEventListener("click", async () => {
  const pct = parseInt(document.getElementById("slider-fan-speed").value, 10) || 60;
  const fb = document.getElementById("fan-feedback");
  fb.textContent = "Ajustando ventos (pode levar até 15 s)...";
  const res = await fetch("/api/hardware/fans", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode: "manual", speed_percent: pct }),
  });
  const data = await res.json();
  fb.textContent = `🌀 ${data.fan_note || `Rotação fixada em ${data.fan_speed_percent}%!`}`;
  fetchTelemetry();
  setTimeout(() => (fb.textContent = ""), 4000);
});

document.getElementById("btn-sophya-auto-hw").addEventListener("click", async () => {
  const fb = document.getElementById("fan-feedback");
  fb.textContent = "🪄 Sophy avaliando temperatura e mana do santuário...";
  const res = await fetch("/api/hardware/auto", { method: "POST" });
  const data = await res.json();
  fb.textContent = `🪄 ${data.decision_summary || "Encantamento concluído!"}`;
  fetchTelemetry();
  setTimeout(() => (fb.textContent = ""), 6000);
});

document.getElementById("btn-activate-uniwill-driver").addEventListener("click", async () => {
  const fb = document.getElementById("fan-feedback");
  fb.textContent = "⚡ Solicitando permissão Polkit (pkexec) para carregar o driver TUXEDO...";
  const res = await fetch("/api/hardware/activate-driver", { method: "POST" });
  const data = await res.json();
  fb.textContent = data.message || "";
  fetchTelemetry();
});

const rgbSlider = document.getElementById("slider-rgb-brightness");
if (rgbSlider) {
  rgbSlider.addEventListener("input", () => {
    document.getElementById("rgb-brightness-label").textContent = `${rgbSlider.value}%`;
  });
}

document.querySelectorAll(".rgb-preset-btn").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const preset = btn.getAttribute("data-rgb-preset");
    const brightness = parseInt(document.getElementById("slider-rgb-brightness").value, 10) ?? 80;
    const fb = document.getElementById("rgb-feedback");
    const res = await fetch("/api/hardware/rgb", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        color_or_preset: preset,
        brightness_percent: preset === "off" ? 0 : brightness,
        effect: preset === "off" ? "off" : "static",
      }),
    });
    const data = await res.json();
    if (data.color_hex && data.color_hex !== "#000000") {
      document.getElementById("input-rgb-color").value = data.color_hex;
    }
    fb.textContent = `✨ ${data.rgb_note || "Cor aplicada!"}`;
    fetchTelemetry();
    setTimeout(() => (fb.textContent = ""), 4500);
  });
});

document.getElementById("btn-apply-rgb").addEventListener("click", async () => {
  const colorHex = document.getElementById("input-rgb-color").value;
  const effect = document.getElementById("select-rgb-effect").value;
  const brightness = parseInt(document.getElementById("slider-rgb-brightness").value, 10) ?? 80;
  const fb = document.getElementById("rgb-feedback");
  const res = await fetch("/api/hardware/rgb", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      color_or_preset: colorHex,
      brightness_percent: brightness,
      effect,
    }),
  });
  const data = await res.json();
  fb.textContent = `✨ ${data.rgb_note || "Iluminação atualizada!"}`;
  fetchTelemetry();
  setTimeout(() => (fb.textContent = ""), 4500);
});

// Inicialização
loadConfig();
fetchTelemetry();
loadWorkersLogs();
refreshRealWhatsAppQR();
setInterval(refreshRealWhatsAppQR, 4000);
