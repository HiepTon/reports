"""On-page "Rebuild digest" control.

Triggers a GitHub Actions workflow_dispatch straight from the browser, mirroring
the reader's key pattern: the visitor supplies a GitHub token once (kept in
localStorage). Only a token with write access to the repo can dispatch, so this
is safe to ship on a public page, and no backend is needed — GitHub's REST API
allows CORS browser requests. Reuses the reader-* CSS classes for styling.
"""

from __future__ import annotations

import json

REBUILD_REPO_DEFAULT = "HiepTon/reports"
REBUILD_REF_DEFAULT = "main"

# (workflow filename, EN label, VI label)
REBUILD_WORKFLOWS = [
    ("vietnam-news-daily.yml", "Vietnam digest", "Tin Việt Nam"),
    ("security-news-daily.yml", "Security digest", "Tin an ninh mạng"),
]


def digest_rebuild_css() -> str:
    return """
    .rebuild-select { flex: 0 1 12rem; min-width: 8rem; cursor: pointer; }
    #rebuildStatus { flex: 1 1 12rem; font-size: 0.8rem; color: var(--muted); margin: 0; min-height: 1.2em; }
    #rebuildStatus.err { color: #f0a4a4; }
"""


def digest_rebuild_toolbar_inner(
    *,
    lang: str,
    selected: str | None = None,
    workflows: list[tuple[str, str, str]] | None = None,
) -> str:
    wfs = workflows or REBUILD_WORKFLOWS
    vi = lang == "vi"
    opts = []
    for filename, label_en, label_vi in wfs:
        sel = " selected" if selected and filename == selected else ""
        label = label_vi if vi else label_en
        opts.append(f'<option value="{filename}"{sel}>{label}</option>')
    options_html = "".join(opts)
    if vi:
        return f"""<div class="reader-tools rebuild-tools">
      <div class="reader-key-row">
        <label for="rebuildTokenInput">GitHub token</label>
        <input type="password" id="rebuildTokenInput" class="reader-api-input" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="Token có quyền Actions ghi"/>
        <button type="button" class="reset reader-save-key" id="saveRebuildToken">Lưu</button>
        <span id="rebuildTokenHint" class="reader-key-hint" aria-live="polite"></span>
      </div>
      <div class="reader-actions-row">
        <label for="rebuildWorkflow">Bản tin</label>
        <select id="rebuildWorkflow" class="reader-api-input rebuild-select">{options_html}</select>
        <button type="button" class="apply read" id="rebuildRun">Tạo lại</button>
        <p id="rebuildStatus"></p>
      </div>
    </div>"""
    return f"""<div class="reader-tools rebuild-tools">
      <div class="reader-key-row">
        <label for="rebuildTokenInput">GitHub token</label>
        <input type="password" id="rebuildTokenInput" class="reader-api-input" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="Token with Actions write access"/>
        <button type="button" class="reset reader-save-key" id="saveRebuildToken">Save</button>
        <span id="rebuildTokenHint" class="reader-key-hint" aria-live="polite"></span>
      </div>
      <div class="reader-actions-row">
        <label for="rebuildWorkflow">Digest</label>
        <select id="rebuildWorkflow" class="reader-api-input rebuild-select">{options_html}</select>
        <button type="button" class="apply read" id="rebuildRun">Rebuild</button>
        <p id="rebuildStatus"></p>
      </div>
    </div>"""


def digest_rebuild_script(
    *,
    lang: str,
    repo: str = REBUILD_REPO_DEFAULT,
    ref: str = REBUILD_REF_DEFAULT,
) -> str:
    """Inline script that dispatches the selected workflow via the GitHub REST API."""
    l = json.dumps(lang)
    rp = json.dumps(repo)
    rf = json.dumps(ref)
    return f"""
(function() {{
  var LANG = {l};
  var REPO = {rp};
  var REF = {rf};
  var KEY_TOKEN = "reportsDigestRebuildGithubToken";

  function status(msg, isErr) {{
    var el = document.getElementById("rebuildStatus");
    if (!el) return;
    el.textContent = msg || "";
    el.className = isErr ? "err" : "";
  }}
  function hint(msg) {{
    var el = document.getElementById("rebuildTokenHint");
    if (el) el.textContent = msg || "";
  }}
  function readToken() {{
    try {{ var v = localStorage.getItem(KEY_TOKEN); return v ? String(v).trim() : ""; }} catch (e) {{ return ""; }}
  }}
  function getToken() {{
    var s = readToken();
    if (s) return s;
    var inp = document.getElementById("rebuildTokenInput");
    return inp ? String(inp.value || "").trim() : "";
  }}
  function saveToken() {{
    var inp = document.getElementById("rebuildTokenInput");
    var t = inp ? String(inp.value || "").trim() : "";
    if (!t) {{ hint(LANG === "vi" ? "Nhập token rồi Lưu." : "Enter a token, then Save."); return; }}
    try {{ localStorage.setItem(KEY_TOKEN, t); }} catch (e) {{
      hint(LANG === "vi" ? "Không lưu được (localStorage)." : "Could not save (localStorage)."); return;
    }}
    if (inp) inp.value = "";
    hint(LANG === "vi" ? "Đã lưu token trên trình duyệt này." : "Token saved on this browser.");
  }}

  async function runRebuild() {{
    var token = getToken();
    if (!token) {{
      status(LANG === "vi" ? "Nhập và lưu GitHub token để chạy." : "Enter and save a GitHub token to run.", true);
      var f = document.getElementById("rebuildTokenInput");
      if (f) f.focus();
      return;
    }}
    var sel = document.getElementById("rebuildWorkflow");
    var wf = sel ? sel.value : "";
    if (!wf) {{ status(LANG === "vi" ? "Chọn bản tin để tạo lại." : "Choose a digest to rebuild.", true); return; }}
    var btn = document.getElementById("rebuildRun");
    if (btn) btn.disabled = true;
    status(LANG === "vi" ? "Đang gửi yêu cầu…" : "Dispatching…");
    try {{
      var url = "https://api.github.com/repos/" + REPO + "/actions/workflows/" + encodeURIComponent(wf) + "/dispatches";
      var resp = await fetch(url, {{
        method: "POST",
        headers: {{
          "Accept": "application/vnd.github+json",
          "Authorization": "Bearer " + token,
          "X-GitHub-Api-Version": "2022-11-28",
          "Content-Type": "application/json"
        }},
        body: JSON.stringify({{ ref: REF }})
      }});
      if (resp.status === 204) {{
        status(LANG === "vi"
          ? "Đã kích hoạt build ✓ (chạy vài phút; tải lại trang sau khi xong)."
          : "Build queued ✓ (takes a few minutes; reload the page when it finishes).", false);
      }} else {{
        var detail = "";
        try {{ var j = await resp.json(); detail = j && j.message ? j.message : ""; }} catch (e) {{}}
        var base = (LANG === "vi" ? "Không kích hoạt được (HTTP " : "Dispatch failed (HTTP ") + resp.status + ")";
        if (resp.status === 401 || resp.status === 403) base += LANG === "vi" ? " — token thiếu quyền Actions ghi." : " — token lacks Actions write access.";
        else if (resp.status === 404) base += LANG === "vi" ? " — sai repo/workflow hoặc token không có quyền." : " — wrong repo/workflow, or the token can't see it.";
        status(detail ? base + ": " + detail : base, true);
      }}
    }} catch (e) {{
      status(String(e && e.message ? e.message : e), true);
    }} finally {{
      if (btn) btn.disabled = false;
    }}
  }}

  var runBtn = document.getElementById("rebuildRun");
  var saveBtn = document.getElementById("saveRebuildToken");
  var tokInp = document.getElementById("rebuildTokenInput");
  if (runBtn) runBtn.addEventListener("click", function() {{ runRebuild().catch(function(e) {{ status(String(e), true); }}); }});
  if (saveBtn) saveBtn.addEventListener("click", saveToken);
  if (tokInp) tokInp.addEventListener("keydown", function(ev) {{ if (ev.key === "Enter") {{ ev.preventDefault(); saveToken(); }} }});
  if (readToken()) hint(LANG === "vi" ? "Đã lưu GitHub token trên trình duyệt này." : "GitHub token saved on this browser.");
}})();
"""
