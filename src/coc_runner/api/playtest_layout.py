from __future__ import annotations

from html import escape

from fastapi.responses import HTMLResponse


PLAYTEST_PAGE_STYLES = """
:root {
  color-scheme: light;
  --bg: #f2efe7;
  --card: #fffdf8;
  --ink: #2b2118;
  --muted: #6b5b4d;
  --line: #d7cab8;
  --accent: #6c4f3d;
  --danger: #8b2f2f;
  --success: #245c3d;
  --warn: #7a5b11;
}
body {
  margin: 0;
  font-family: "Microsoft YaHei UI", "Noto Sans SC", sans-serif;
  background: linear-gradient(180deg, #efe7d6 0%, var(--bg) 100%);
  color: var(--ink);
}
main {
  max-width: 1040px;
  margin: 0 auto;
  padding: 32px 20px 48px;
}
.hero, .panel, .checkpoint-card, .feedback, .attention-card, .activity-item, .checkpoint-summary-item {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 16px;
  box-shadow: 0 10px 30px rgba(43, 33, 24, 0.06);
}
.hero, .panel, .feedback {
  padding: 20px;
  margin-bottom: 18px;
}
.hero h1 {
  margin: 0 0 8px;
  font-size: 28px;
}
.hero-meta, .nav-links, .quick-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 18px;
  color: var(--muted);
}
.nav-links {
  margin-top: 14px;
}
.panel h2, .panel h3 {
  margin-top: 0;
}
form {
  display: grid;
  gap: 12px;
}
label {
  display: grid;
  gap: 6px;
  font-size: 14px;
}
input, textarea, button {
  font: inherit;
}
input, textarea {
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 10px 12px;
  background: #fff;
}
button, .action-link {
  border: none;
  border-radius: 999px;
  padding: 10px 14px;
  background: var(--accent);
  color: #fff;
  cursor: pointer;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
button.danger, .action-link.danger {
  background: var(--danger);
}
button:disabled {
  opacity: 0.6;
  cursor: wait;
}
.checkpoint-list, .attention-grid, .recent-list, .checkpoint-summary-list {
  display: grid;
  gap: 14px;
}
.dashboard-grid {
  display: grid;
  gap: 18px;
}
.summary-grid {
  display: grid;
  gap: 14px;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
}
.summary-card {
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.75);
}
.summary-card h3 {
  margin: 0 0 10px;
  font-size: 15px;
}
.summary-card ul, .recent-list ul, .attention-card ul, .warning-box ul {
  margin: 0;
  padding-left: 18px;
}
.summary-card li, .recent-list li, .attention-card li, .warning-box li {
  margin-bottom: 6px;
}
.checkpoint-card, .attention-card, .activity-item, .checkpoint-summary-item {
  padding: 18px;
}
.checkpoint-card-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.checkpoint-card-header h3 {
  margin: 0 0 6px;
}
.checkpoint-meta {
  display: grid;
  gap: 6px;
  min-width: 180px;
  color: var(--muted);
  font-size: 13px;
  text-align: right;
}
.checkpoint-actions {
  display: grid;
  gap: 14px;
}
.checkpoint-secondary-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.feedback-success {
  border-color: rgba(36, 92, 61, 0.25);
}
.feedback-error {
  border-color: rgba(139, 47, 47, 0.25);
}
.feedback-code, .muted, .empty-state, .help, .meta-line, .activity-meta {
  color: var(--muted);
}
.warning-box {
  margin-top: 14px;
  padding: 14px;
  border-radius: 12px;
  background: rgba(122, 91, 17, 0.08);
  color: var(--warn);
}
.warning-box h3, .warning-box p {
  margin-top: 0;
}
.status-pill {
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  border-radius: 999px;
  background: rgba(108, 79, 61, 0.12);
  color: var(--accent);
  font-size: 13px;
}
.status-pill.warn {
  background: rgba(122, 91, 17, 0.12);
  color: var(--warn);
}
.activity-item h3, .attention-card h3, .checkpoint-summary-item h3 {
  margin: 0 0 8px;
  font-size: 16px;
}
.checkpoint-summary-item p, .attention-card p, .activity-item p {
  margin: 0 0 8px;
}
.checkpoint-summary-header, .activity-header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: baseline;
}
code, .mono {
  background: rgba(43, 33, 24, 0.08);
  padding: 2px 6px;
  border-radius: 6px;
}
a {
  color: var(--accent);
}
@media (max-width: 720px) {
  .checkpoint-card-header, .checkpoint-summary-header, .activity-header {
    display: grid;
  }
  .checkpoint-meta {
    text-align: left;
    min-width: 0;
  }
}
"""

PLAYTEST_FORM_SCRIPT = """
document.querySelectorAll("form[data-submit-label]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    const confirmMessage = form.dataset.confirm;
    if (confirmMessage && !window.confirm(confirmMessage)) {
      event.preventDefault();
      return;
    }
    const button = form.querySelector("button[type='submit']");
    if (!button) {
      return;
    }
    button.disabled = true;
    button.dataset.originalText = button.textContent;
    button.textContent = form.dataset.submitLabel || "处理中...";
  });
});
"""


def render_playtest_shell(
    *,
    title: str,
    body: str,
    status_code: int = 200,
    include_form_script: bool = False,
) -> HTMLResponse:
    script = f"<script>{PLAYTEST_FORM_SCRIPT}</script>" if include_form_script else ""
    html = f"""
    <!doctype html>
    <html lang="zh-CN">
      <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>{escape(title)}</title>
        <style>{PLAYTEST_PAGE_STYLES}</style>
      </head>
      <body>
        <main>{body}</main>
        {script}
      </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=status_code)
