from __future__ import annotations

from html import escape

from fastapi.responses import HTMLResponse


WEB_APP_SHELL_STYLES = """
:root {
  color-scheme: light;
  --bg: #f3ece0;
  --bg-deep: #e4d6c3;
  --surface: rgba(255, 251, 245, 0.94);
  --surface-strong: #fffdf8;
  --surface-muted: rgba(113, 88, 65, 0.08);
  --ink: #221b16;
  --muted: #6e6054;
  --line: rgba(108, 88, 66, 0.18);
  --accent: #6a3f2b;
  --accent-2: #245a57;
  --danger: #8b2f2f;
  --warn: #8a6512;
  --success: #1f6644;
  --shadow: 0 18px 48px rgba(34, 27, 22, 0.08);
}
* {
  box-sizing: border-box;
}
body {
  margin: 0;
  min-height: 100vh;
  color: var(--ink);
  background:
    radial-gradient(circle at top left, rgba(36, 90, 87, 0.10), transparent 36%),
    radial-gradient(circle at top right, rgba(106, 63, 43, 0.12), transparent 30%),
    linear-gradient(180deg, var(--bg) 0%, var(--bg-deep) 100%);
  font-family: "Microsoft YaHei UI", "Noto Sans SC", sans-serif;
}
a {
  color: inherit;
}
.shell {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
}
.sidebar {
  position: sticky;
  top: 0;
  align-self: start;
  min-height: 100vh;
  padding: 24px 20px 32px;
  background:
    linear-gradient(180deg, rgba(255, 249, 241, 0.96) 0%, rgba(245, 235, 220, 0.92) 100%);
  border-right: 1px solid var(--line);
  backdrop-filter: blur(10px);
}
.brand {
  margin-bottom: 18px;
  padding: 18px;
  border-radius: 20px;
  background: linear-gradient(145deg, rgba(36, 90, 87, 0.12), rgba(106, 63, 43, 0.08));
  border: 1px solid rgba(36, 90, 87, 0.12);
}
.brand-kicker {
  margin: 0 0 6px;
  font-size: 12px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--accent-2);
}
.brand h1 {
  margin: 0 0 8px;
  font-size: 27px;
  line-height: 1.1;
  font-family: "Georgia", "Noto Serif SC", serif;
}
.brand p {
  margin: 0;
  color: var(--muted);
  line-height: 1.55;
}
.nav-group {
  margin-top: 20px;
}
.nav-group h2,
.context-card h2 {
  margin: 0 0 10px;
  font-size: 13px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--muted);
}
.nav-stack,
.context-stack,
.link-stack,
.card-list,
.meta-list,
.toolbar,
.metric-grid,
.content-grid,
.surface-grid,
.pill-row {
  display: grid;
  gap: 12px;
}
.nav-stack {
  gap: 8px;
}
.nav-link,
.context-link,
.button-link,
.button-button {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 14px;
  text-decoration: none;
  border: 1px solid transparent;
  transition: border-color 0.15s ease, background 0.15s ease, transform 0.15s ease;
}
.nav-link {
  background: transparent;
  color: var(--muted);
}
.nav-link:hover,
.context-link:hover,
.button-link:hover,
.button-button:hover {
  border-color: var(--line);
  background: rgba(255, 255, 255, 0.7);
  transform: translateY(-1px);
}
.nav-link.active {
  color: #fff;
  background: linear-gradient(135deg, var(--accent), #8c5a40);
  box-shadow: 0 14px 30px rgba(106, 63, 43, 0.22);
}
.nav-link.disabled {
  opacity: 0.55;
}
.nav-label {
  font-weight: 700;
}
.nav-meta,
.muted,
.empty,
.meta-line,
.list-meta,
.helper {
  color: var(--muted);
}
.sidebar .section-card,
.surface,
.context-card {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 20px;
  box-shadow: var(--shadow);
}
.context-card,
.surface {
  padding: 18px;
}
.context-card p,
.surface p {
  margin: 0;
}
.content {
  padding: 28px 30px 48px;
}
.page-head {
  margin-bottom: 20px;
  padding: 24px 26px;
  border-radius: 24px;
  background:
    linear-gradient(140deg, rgba(255, 253, 248, 0.95) 0%, rgba(248, 240, 228, 0.92) 100%);
  border: 1px solid var(--line);
  box-shadow: var(--shadow);
}
.eyebrow {
  margin: 0 0 8px;
  font-size: 12px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--accent-2);
}
.page-head h1 {
  margin: 0;
  font-size: 34px;
  line-height: 1.08;
  font-family: "Georgia", "Noto Serif SC", serif;
}
.page-head p {
  margin: 12px 0 0;
  max-width: 70ch;
  color: var(--muted);
  line-height: 1.6;
}
.toolbar {
  margin-top: 18px;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
}
.button-link,
.button-button {
  justify-content: center;
  font-weight: 700;
  background: linear-gradient(135deg, var(--accent), #8c5a40);
  color: #fff;
  border-color: transparent;
}
.button-link.secondary,
.button-button.secondary {
  background: linear-gradient(135deg, var(--accent-2), #3c7d79);
}
.button-link.ghost,
.button-button.ghost {
  color: var(--ink);
  background: rgba(255, 255, 255, 0.74);
  border-color: var(--line);
}
.button-button {
  width: 100%;
  cursor: pointer;
  font: inherit;
}
.button-button.warn {
  background: linear-gradient(135deg, var(--warn), #b5851f);
}
.button-button.danger {
  background: linear-gradient(135deg, var(--danger), #b34949);
}
.content-grid {
  grid-template-columns: minmax(0, 1.45fr) minmax(280px, 0.95fr);
  align-items: start;
}
.surface-grid {
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
}
.surface h2 {
  margin: 0 0 10px;
  font-size: 22px;
  line-height: 1.2;
  font-family: "Georgia", "Noto Serif SC", serif;
}
.surface h3 {
  margin: 0 0 8px;
  font-size: 17px;
}
.surface-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 14px;
  margin-bottom: 12px;
}
.surface-header p {
  color: var(--muted);
}
.metric-grid {
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
}
.metric {
  padding: 14px;
  border-radius: 16px;
  background: var(--surface-muted);
  border: 1px solid rgba(106, 63, 43, 0.08);
}
.metric-label {
  margin: 0;
  font-size: 12px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--muted);
}
.metric strong {
  display: block;
  margin-top: 8px;
  font-size: 24px;
  line-height: 1.1;
}
.metric span {
  display: block;
  margin-top: 6px;
  color: var(--muted);
}
.card-list {
  gap: 10px;
}
.list-card {
  padding: 14px 16px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid var(--line);
}
.list-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: baseline;
  margin-bottom: 8px;
}
.list-head h3 {
  margin: 0;
  font-size: 16px;
}
.list-card p {
  margin: 0;
  line-height: 1.55;
}
.list-card ul,
.surface ul,
.meta-list {
  margin: 0;
  padding-left: 18px;
}
.meta-list li,
.surface li {
  margin-bottom: 6px;
}
.list-meta,
.status-pill,
.tag {
  font-size: 13px;
}
.status-pill,
.tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  border-radius: 999px;
  background: rgba(36, 90, 87, 0.1);
  color: var(--accent-2);
}
.status-pill.warn,
.tag.warn {
  background: rgba(138, 101, 18, 0.12);
  color: var(--warn);
}
.status-pill.danger,
.tag.danger {
  background: rgba(139, 47, 47, 0.12);
  color: var(--danger);
}
.status-pill.success,
.tag.success {
  background: rgba(31, 102, 68, 0.12);
  color: var(--success);
}
.inline-meta,
.pill-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.detail-error {
  margin-bottom: 18px;
  padding: 16px 18px;
  border-radius: 18px;
  border: 1px solid rgba(139, 47, 47, 0.18);
  background: rgba(139, 47, 47, 0.08);
  color: var(--danger);
}
.detail-error h2 {
  margin: 0 0 6px;
  font-size: 18px;
  font-family: "Georgia", "Noto Serif SC", serif;
}
.detail-error p,
.detail-error li {
  margin: 0;
  line-height: 1.55;
}
.detail-error ul {
  margin: 10px 0 0;
  padding-left: 18px;
}
.notice-panel,
.feedback-panel {
  margin-bottom: 18px;
  padding: 16px 18px;
  border-radius: 18px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.8);
}
.notice-panel.success,
.feedback-panel.success {
  border-color: rgba(31, 102, 68, 0.2);
  background: rgba(31, 102, 68, 0.08);
}
.feedback-panel h2,
.notice-panel h2 {
  margin: 0 0 8px;
  font-size: 18px;
  font-family: "Georgia", "Noto Serif SC", serif;
}
.feedback-panel p,
.feedback-panel li,
.notice-panel p {
  margin: 0;
  line-height: 1.55;
}
.feedback-panel ul {
  margin: 10px 0 0;
  padding-left: 18px;
}
.form-grid,
.form-stack,
.field-grid,
.inline-form-grid,
.radio-grid {
  display: grid;
  gap: 12px;
}
.field-grid {
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
}
.radio-grid {
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
}
.form-stack {
  gap: 14px;
}
.inline-form-grid {
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
}
form {
  display: grid;
  gap: 12px;
}
label,
fieldset {
  display: grid;
  gap: 8px;
}
input,
select,
textarea {
  width: 100%;
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.92);
  color: var(--ink);
  font: inherit;
}
textarea {
  resize: vertical;
}
fieldset {
  margin: 0;
  padding: 14px;
  border-radius: 16px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.52);
}
legend {
  padding: 0 6px;
  color: var(--muted);
}
.checkbox-line {
  display: flex;
  align-items: center;
  gap: 10px;
}
.checkbox-line input {
  width: auto;
}
.radio-card {
  display: grid;
  gap: 8px;
  padding: 14px;
  border-radius: 16px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.72);
}
.radio-card input {
  width: auto;
}
.radio-card strong {
  display: block;
}
.action-stack {
  display: grid;
  gap: 14px;
}
.adoption-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}
.adoption-status {
  margin: 8px 0 0;
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px dashed var(--line);
  background: rgba(255, 255, 255, 0.54);
}
.assistant-context-preview {
  margin: 12px 0;
  padding: 12px;
  border-radius: 14px;
  border: 1px solid var(--line);
  background: rgba(255, 250, 242, 0.76);
}
.assistant-context-preview .meta-list {
  margin: 8px 0 0;
}
.assistant-source-echo {
  margin: 12px 0;
  padding: 12px;
  border-radius: 14px;
  border: 1px dashed rgba(133, 94, 60, 0.38);
  background: rgba(246, 241, 231, 0.92);
}
.assistant-source-echo .meta-list {
  margin: 8px 0 0;
}
.assistant-flow-status {
  margin: 10px 0 0;
  padding-top: 10px;
  border-top: 1px dashed rgba(133, 94, 60, 0.22);
}
.assistant-completion-status {
  margin: 10px 0 0;
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid rgba(49, 107, 72, 0.22);
  background: rgba(237, 247, 239, 0.92);
}
.assistant-draft-source {
  position: absolute;
  left: -9999px;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}
.divider {
  height: 1px;
  margin: 8px 0 2px;
  background: var(--line);
}
code,
.mono {
  padding: 2px 6px;
  border-radius: 6px;
  background: rgba(34, 27, 22, 0.08);
}
@media (max-width: 980px) {
  .shell {
    grid-template-columns: 1fr;
  }
  .sidebar {
    position: static;
    min-height: auto;
    border-right: none;
    border-bottom: 1px solid var(--line);
  }
  .content {
    padding: 22px 18px 42px;
  }
  .content-grid {
    grid-template-columns: 1fr;
  }
}
"""

WEB_APP_SHELL_SCRIPT = """
(() => {
  document.addEventListener("click", (event) => {
    const trigger = event.target instanceof Element
      ? event.target.closest("[data-adopt-source][data-adopt-target]")
      : null;
    if (!(trigger instanceof HTMLButtonElement)) {
      return;
    }
    const sourceId = trigger.getAttribute("data-adopt-source") || "";
    const targetId = trigger.getAttribute("data-adopt-target") || "";
    const statusId = trigger.getAttribute("data-adopt-status") || "";
    const statusText = trigger.getAttribute("data-adopt-status-text") || "";
    const flowStatusId = trigger.getAttribute("data-adopt-flow-status") || "";
    const flowStatusText = trigger.getAttribute("data-adopt-flow-status-text") || "";
    const source = document.getElementById(sourceId);
    const target = document.getElementById(targetId);
    if (!(source instanceof HTMLTextAreaElement) || !(target instanceof HTMLTextAreaElement)) {
      return;
    }
    target.value = source.value;
    target.focus();
    target.setSelectionRange(target.value.length, target.value.length);
    target.dispatchEvent(new Event("input", { bubbles: true }));
    const statusNode = statusId ? document.getElementById(statusId) : null;
    if (statusNode) {
      statusNode.textContent = statusText;
    }
    const flowStatusNode = flowStatusId ? document.getElementById(flowStatusId) : null;
    if (flowStatusNode) {
      flowStatusNode.textContent = flowStatusText;
    }
  });
})();
"""


def render_web_app_shell(
    *,
    title: str,
    sidebar_html: str,
    body_html: str,
    status_code: int = 200,
) -> HTMLResponse:
    html = f"""
    <!doctype html>
    <html lang="zh-CN">
      <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>{escape(title)}</title>
        <style>{WEB_APP_SHELL_STYLES}</style>
      </head>
      <body>
        <div class="shell">
          <aside class="sidebar">{sidebar_html}</aside>
          <main class="content">{body_html}</main>
        </div>
        <script>{WEB_APP_SHELL_SCRIPT}</script>
      </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=status_code)
