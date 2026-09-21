"""Shared HTML/CSS/JS for the on-page "Read news" control.

The reader speaks the summaries already baked into each card by the CI build
(no in-browser LLM). Audio is synthesized client-side with the Azure AI Speech
SDK, which is the CORS-safe way to call Azure TTS from a browser. The visitor
supplies an Azure Speech resource key + region once (kept in localStorage so it
persists across visits until the visitor edits or clears it).
"""

from __future__ import annotations

import json

# Azure Neural voice ids (see Azure AI Speech voice list).
READ_NEWS_AZURE_VOICE_EN_DEFAULT = "en-US-AriaNeural"
READ_NEWS_AZURE_VOICE_VI_DEFAULT = "vi-VN-HoaiMyNeural"
READ_NEWS_AZURE_VOICE_FALLBACK_EN_DEFAULT = "en-US-JennyNeural"
READ_NEWS_AZURE_VOICE_FALLBACK_VI_DEFAULT = "vi-VN-NamMinhNeural"

# Official browser bundle (redirects to the Azure CDN); self-hosted page has no CSP.
AZURE_SPEECH_SDK_URL = "https://aka.ms/csspeech/jsbrowserpackageraw"


def digest_reader_sdk_script_tag() -> str:
    """The <script> tag that loads the Azure Speech SDK browser bundle (window.SpeechSDK)."""
    return f'<script src="{AZURE_SPEECH_SDK_URL}"></script>'


def digest_reader_css() -> str:
    return """
    .reader-tools { display: flex; flex-direction: column; gap: 0.55rem; width: 100%; margin-top: 0.65rem; padding-top: 0.65rem; border-top: 1px solid var(--border); }
    .reader-key-row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.45rem 0.65rem; width: 100%; font-size: 0.88rem; }
    .reader-key-row label { color: var(--muted); font-weight: 600; }
    .reader-api-input {
      flex: 1 1 12rem; min-width: 9rem; max-width: 100%; padding: 0.4rem 0.55rem; border-radius: 6px;
      border: 1px solid var(--border); background: var(--bg); color: var(--text); font-size: 0.88rem;
    }
    .reader-region-input { flex: 0 1 8rem; min-width: 6rem; }
    .reader-save-key { flex: 0 0 auto; }
    .reader-key-hint { flex: 1 1 10rem; font-size: 0.78rem; color: var(--muted); }
    .reader-actions-row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 0.75rem; }
    .reader-speed-wrap {
      display: inline-flex; align-items: center; gap: 0.35rem; flex-wrap: wrap;
      padding: 0.15rem 0.35rem; border-radius: 6px; border: 1px solid var(--border); background: rgba(0,0,0,0.12);
    }
    .reader-speed-wrap .reader-speed-heading { color: var(--muted); font-size: 0.78rem; font-weight: 600; margin: 0; }
    .reader-speed-btn {
      cursor: pointer; min-width: 2rem; padding: 0.3rem 0.45rem; border-radius: 5px;
      border: 1px solid var(--border); background: var(--bg); color: var(--text); font-size: 0.95rem; font-weight: 700; line-height: 1;
    }
    .reader-speed-btn:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
    .reader-speed-btn:disabled { opacity: 0.4; cursor: not-allowed; }
    #readerSpeedValue {
      min-width: 3rem; text-align: center; font-size: 0.82rem; font-variant-numeric: tabular-nums;
      font-weight: 650; color: var(--text);
    }
    .reader-tools .read { background: var(--accent); color: #0a111a; }
    .reader-tools .stop { background: transparent; color: var(--muted); border: 1px solid var(--border); }
    .reader-tools .read:disabled, .reader-tools .stop:disabled { opacity: 0.45; cursor: not-allowed; }
    #readStatus { flex: 1 1 12rem; font-size: 0.8rem; color: var(--muted); margin: 0; min-height: 1.2em; }
    #readStatus.err { color: #f0a4a4; }
"""


def digest_reader_toolbar_inner(*, lang: str) -> str:
    if lang == "vi":
        return """<div class="reader-tools">
      <div class="reader-key-row">
        <label for="readerAzureKeyInput">Khóa Azure Speech</label>
        <input type="password" id="readerAzureKeyInput" class="reader-api-input" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="Khóa tài nguyên Speech"/>
        <label for="readerAzureRegionInput">Vùng</label>
        <input type="text" id="readerAzureRegionInput" class="reader-api-input reader-region-input" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="vd: southeastasia"/>
        <button type="button" class="reset reader-save-key" id="saveReaderApiKey">Lưu</button>
        <span id="readerKeySavedHint" class="reader-key-hint" aria-live="polite"></span>
      </div>
      <div class="reader-actions-row">
        <button type="button" class="apply read" id="readNews">Đọc tin</button>
        <button type="button" class="reset stop" id="stopRead" disabled>Dừng đọc</button>
        <div class="reader-speed-wrap" role="group" aria-label="Tốc độ đọc">
          <span class="reader-speed-heading">Tốc độ</span>
          <button type="button" class="reader-speed-btn" id="readerSpeedDown" title="Chậm hơn" aria-label="Chậm hơn">−</button>
          <span id="readerSpeedValue">1.0×</span>
          <button type="button" class="reader-speed-btn" id="readerSpeedUp" title="Nhanh hơn" aria-label="Nhanh hơn">+</button>
        </div>
        <p id="readStatus"></p>
      </div>
    </div>"""
    return """<div class="reader-tools">
      <div class="reader-key-row">
        <label for="readerAzureKeyInput">Azure Speech key</label>
        <input type="password" id="readerAzureKeyInput" class="reader-api-input" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="Speech resource key"/>
        <label for="readerAzureRegionInput">Region</label>
        <input type="text" id="readerAzureRegionInput" class="reader-api-input reader-region-input" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="e.g. eastus"/>
        <button type="button" class="reset reader-save-key" id="saveReaderApiKey">Save</button>
        <span id="readerKeySavedHint" class="reader-key-hint" aria-live="polite"></span>
      </div>
      <div class="reader-actions-row">
        <button type="button" class="apply read" id="readNews">Read news</button>
        <button type="button" class="reset stop" id="stopRead" disabled>Stop</button>
        <div class="reader-speed-wrap" role="group" aria-label="Reading speed">
          <span class="reader-speed-heading">Speed</span>
          <button type="button" class="reader-speed-btn" id="readerSpeedDown" title="Slower" aria-label="Slower">−</button>
          <span id="readerSpeedValue">1.0×</span>
          <button type="button" class="reader-speed-btn" id="readerSpeedUp" title="Faster" aria-label="Faster">+</button>
        </div>
        <p id="readStatus"></p>
      </div>
    </div>"""


def digest_reader_script(
    *,
    lang: str,
    voice: str,
    voice_fallback: str | None = None,
    region_default: str | None = None,
) -> str:
    """Inline reader script. Reads baked card text and synthesizes via the Azure Speech SDK."""
    vf_raw = voice_fallback or (
        READ_NEWS_AZURE_VOICE_FALLBACK_VI_DEFAULT if lang == "vi" else READ_NEWS_AZURE_VOICE_FALLBACK_EN_DEFAULT
    )
    v = json.dumps(voice)
    vf = json.dumps(vf_raw)
    l = json.dumps(lang)
    rd = json.dumps(region_default or "")
    return f"""
(function() {{
  var LANG = {l};
  var VOICE = {v};
  var VOICE_FALLBACK = {vf};
  var DEFAULT_REGION = {rd};
  try {{
    var sp = new URLSearchParams(window.location.search || "");
    var rv = sp.get("readerVoice");
    var rvf = sp.get("readerVoiceFallback");
    var rr = sp.get("readerRegion");
    if (rv && String(rv).trim()) VOICE = String(rv).trim();
    if (rvf && String(rvf).trim()) VOICE_FALLBACK = String(rvf).trim();
    if (rr && String(rr).trim()) DEFAULT_REGION = String(rr).trim();
  }} catch (e) {{}}
  var KEY_AZURE = "reportsDigestReaderAzureKey";
  var KEY_REGION = "reportsDigestReaderAzureRegion";
  var readAborted = false;
  var currentAudio = null;
  var RATE_STORAGE_KEY = "reportsDigestReaderSpeechRate";
  var RATE_MIN = 0.5;
  var RATE_MAX = 2;
  var RATE_STEP = 0.25;
  var speechRateCached = 1;

  function clampSpeechRate(r) {{
    if (!isFinite(r)) return 1;
    return Math.min(RATE_MAX, Math.max(RATE_MIN, Math.round(r / RATE_STEP) * RATE_STEP));
  }}
  function persistSpeechRate(r) {{
    r = clampSpeechRate(r);
    try {{ sessionStorage.setItem(RATE_STORAGE_KEY, String(r)); }} catch (e) {{}}
    return r;
  }}
  function loadSpeechRateOnce() {{
    var fromQs = null;
    try {{
      var sp = new URLSearchParams(window.location.search || "");
      var qs = sp.get("readerSpeed");
      if (qs != null && String(qs).trim() !== "") {{
        var q = parseFloat(String(qs).replace(",", "."));
        if (isFinite(q)) fromQs = clampSpeechRate(q);
      }}
    }} catch (e) {{}}
    if (fromQs != null) {{ speechRateCached = persistSpeechRate(fromQs); return; }}
    try {{
      var raw = sessionStorage.getItem(RATE_STORAGE_KEY);
      if (raw != null && String(raw).trim() !== "") {{
        var val = parseFloat(String(raw).replace(",", "."));
        if (isFinite(val)) {{ speechRateCached = clampSpeechRate(val); return; }}
      }}
    }} catch (e) {{}}
    speechRateCached = 1;
  }}
  function getSpeechRate() {{ return speechRateCached; }}
  function formatSpeechRateDisplay(r) {{
    var x = clampSpeechRate(r);
    var s = (Math.round(x * 100) / 100).toString();
    if (s.indexOf(".") === -1) s += ".0";
    return s + "\\u00d7";
  }}
  function refreshSpeechRateUI() {{
    var el = document.getElementById("readerSpeedValue");
    var dn = document.getElementById("readerSpeedDown");
    var up = document.getElementById("readerSpeedUp");
    var rr = getSpeechRate();
    if (el) el.textContent = formatSpeechRateDisplay(rr);
    if (dn) dn.disabled = rr <= RATE_MIN + 1e-9;
    if (up) up.disabled = rr >= RATE_MAX - 1e-9;
    applySpeechRateToPlayingAudio();
  }}
  function applySpeechRateToPlayingAudio() {{
    if (currentAudio && currentAudio.playbackRate != null) {{
      try {{ currentAudio.playbackRate = getSpeechRate(); }} catch (e) {{}}
    }}
  }}
  function bumpSpeechRate(delta) {{
    speechRateCached = persistSpeechRate(getSpeechRate() + delta);
    refreshSpeechRateUI();
  }}
  loadSpeechRateOnce();

  function status(msg, isErr) {{
    var el = document.getElementById("readStatus");
    if (!el) return;
    el.textContent = msg || "";
    el.className = isErr ? "err" : "";
  }}
  function readKeyHint(msg) {{
    var el = document.getElementById("readerKeySavedHint");
    if (el) el.textContent = msg || "";
  }}

  function readStored(k) {{
    // localStorage so the key + region persist across days (until the visitor edits/clears them).
    try {{ var v = localStorage.getItem(k); return v ? String(v).trim() : ""; }} catch (e) {{ return ""; }}
  }}
  function getAzureKey() {{
    var s = readStored(KEY_AZURE);
    if (s) return s;
    var inp = document.getElementById("readerAzureKeyInput");
    return inp ? String(inp.value || "").trim() : "";
  }}
  function getAzureRegion() {{
    var inp = document.getElementById("readerAzureRegionInput");
    var live = inp ? String(inp.value || "").trim() : "";
    if (live) return live;
    var s = readStored(KEY_REGION);
    return s || DEFAULT_REGION || "";
  }}
  function persistReaderKeys() {{
    var keyInp = document.getElementById("readerAzureKeyInput");
    var regInp = document.getElementById("readerAzureRegionInput");
    var key = keyInp ? String(keyInp.value || "").trim() : "";
    var reg = regInp ? String(regInp.value || "").trim() : "";
    if (!key && !reg) {{
      readKeyHint(LANG === "vi" ? "Nhập khóa + vùng rồi Lưu." : "Enter key + region, then Save.");
      return false;
    }}
    try {{
      if (key) localStorage.setItem(KEY_AZURE, key);
      if (reg) localStorage.setItem(KEY_REGION, reg);
    }} catch (e) {{
      readKeyHint(LANG === "vi" ? "Không lưu được (localStorage)." : "Could not save (localStorage).");
      return false;
    }}
    var hk = readStored(KEY_AZURE);
    var hr = readStored(KEY_REGION);
    if (!hk || !hr) {{
      readKeyHint(
        LANG === "vi"
          ? (!hk ? "Thiếu khóa Azure — nhập và Lưu." : "Thiếu vùng — nhập và Lưu.")
          : (!hk ? "Missing Azure key — enter and Save." : "Missing region — enter and Save.")
      );
    }} else {{
      readKeyHint(LANG === "vi" ? "Đã lưu trên trình duyệt này." : "Saved on this browser.");
    }}
    if (keyInp && key) keyInp.value = "";
    return true;
  }}
  function syncKeyHintOnLoad() {{
    var regInp = document.getElementById("readerAzureRegionInput");
    var storedReg = readStored(KEY_REGION) || DEFAULT_REGION;
    if (regInp && storedReg && !regInp.value) regInp.value = storedReg;
    var k = readStored(KEY_AZURE);
    if (k) readKeyHint(LANG === "vi" ? "Đã lưu khóa Azure trên trình duyệt này." : "Azure key saved on this browser.");
  }}

  function visibleCards() {{
    return Array.prototype.slice.call(document.querySelectorAll("article.card")).filter(function(c) {{ return !c.hidden; }});
  }}
  function cardText(card) {{
    var ta = card.querySelector(".topic a");
    var title = ta ? ta.textContent.trim() : "";
    var blocks = card.querySelectorAll(".block p");
    var chunks = [];
    for (var i = 0; i < blocks.length; i++) chunks.push(blocks[i].textContent.trim());
    return (title ? title + ". " : "") + chunks.join(" ");
  }}

  function synthOnce(key, region, text, voiceId) {{
    return new Promise(function(resolve, reject) {{
      var SDK = window.SpeechSDK;
      if (!SDK) {{ reject(new Error(LANG === "vi" ? "Chưa tải được Azure Speech SDK." : "Azure Speech SDK failed to load.")); return; }}
      var cfg;
      try {{
        cfg = SDK.SpeechConfig.fromSubscription(key, region);
        cfg.speechSynthesisVoiceName = voiceId;
        cfg.speechSynthesisOutputFormat = SDK.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3;
      }} catch (e) {{ reject(e); return; }}
      var synth = new SDK.SpeechSynthesizer(cfg, null);
      synth.speakTextAsync(
        text,
        function(result) {{
          try {{
            if (result.reason === SDK.ResultReason.SynthesizingAudioCompleted) {{
              resolve(result.audioData);
            }} else {{
              reject(new Error(result.errorDetails || ("TTS failed: " + result.reason)));
            }}
          }} finally {{ synth.close(); }}
        }},
        function(err) {{ try {{ synth.close(); }} catch (e) {{}} reject(new Error(String(err))); }}
      );
    }});
  }}
  async function synth(key, region, text) {{
    try {{
      return await synthOnce(key, region, text, VOICE);
    }} catch (e1) {{
      if (!VOICE_FALLBACK || VOICE_FALLBACK === VOICE) throw e1;
      console.warn("Reader TTS: primary voice failed, trying fallback", e1);
      return await synthOnce(key, region, text, VOICE_FALLBACK);
    }}
  }}

  function playMp3Buffer(buf) {{
    return new Promise(function(resolve, reject) {{
      if (readAborted) {{ resolve(); return; }}
      var blob = new Blob([buf], {{ type: "audio/mpeg" }});
      var url = URL.createObjectURL(blob);
      var audio = new Audio(url);
      currentAudio = audio;
      try {{ audio.playbackRate = getSpeechRate(); }} catch (e) {{}}
      audio.onended = function() {{ URL.revokeObjectURL(url); currentAudio = null; resolve(); }};
      audio.onerror = function() {{ URL.revokeObjectURL(url); currentAudio = null; reject(new Error("Audio playback failed.")); }};
      audio.play().catch(function(e) {{ URL.revokeObjectURL(url); currentAudio = null; reject(e); }});
    }});
  }}

  async function runRead() {{
    readAborted = false;
    var cards = visibleCards();
    if (!cards.length) {{
      status(LANG === "vi" ? "Không có bài đang hiển thị." : "No articles visible.", true);
      return;
    }}
    var key = getAzureKey();
    var region = getAzureRegion();
    if (!key || !region) {{
      status(
        LANG === "vi"
          ? "Nhập và lưu khóa Azure Speech + vùng để đọc."
          : "Enter and save an Azure Speech key + region to read.",
        true
      );
      var f = document.getElementById(key ? "readerAzureRegionInput" : "readerAzureKeyInput");
      if (f) f.focus();
      return;
    }}

    var readBtn = document.getElementById("readNews");
    var stopBtn = document.getElementById("stopRead");
    if (readBtn) readBtn.disabled = true;
    if (stopBtn) stopBtn.disabled = false;
    if (currentAudio) {{ try {{ currentAudio.pause(); }} catch (e) {{}} currentAudio = null; }}

    var texts = cards.map(cardText).filter(function(t) {{ return t && t.trim(); }});
    try {{
      var prefetch = synth(key, region, texts[0]);
      for (var j = 0; j < texts.length; j++) {{
        if (readAborted) break;
        status((LANG === "vi" ? "Đang tạo giọng (Azure)… " : "Synthesizing speech (Azure)… ") + (j + 1) + "/" + texts.length);
        var buf = await prefetch;
        if (readAborted) break;
        if (j + 1 < texts.length) prefetch = synth(key, region, texts[j + 1]);
        await playMp3Buffer(buf);
      }}
    }} catch (e) {{
      status(String(e && e.message ? e.message : e), true);
      if (readBtn) readBtn.disabled = false;
      if (stopBtn) stopBtn.disabled = true;
      return;
    }}

    if (readAborted) {{
      status(LANG === "vi" ? "Đã hủy." : "Cancelled.", false);
    }} else {{
      status(LANG === "vi" ? "Đã đọc xong." : "Finished reading.", false);
    }}
    if (readBtn) readBtn.disabled = false;
    if (stopBtn) stopBtn.disabled = true;
  }}

  function stopRead() {{
    readAborted = true;
    if (currentAudio) {{
      try {{ currentAudio.pause(); currentAudio.removeAttribute("src"); currentAudio.load(); }} catch (e) {{}}
      currentAudio = null;
    }}
    status(LANG === "vi" ? "Đã dừng." : "Stopped.", false);
    var readBtn = document.getElementById("readNews");
    var stopBtn = document.getElementById("stopRead");
    if (readBtn) readBtn.disabled = false;
    if (stopBtn) stopBtn.disabled = true;
  }}

  var r = document.getElementById("readNews");
  var s = document.getElementById("stopRead");
  var saveBtn = document.getElementById("saveReaderApiKey");
  var keyInp = document.getElementById("readerAzureKeyInput");
  var regInp = document.getElementById("readerAzureRegionInput");
  if (r) r.addEventListener("click", function() {{ runRead().catch(function(e) {{ status(String(e), true); }}); }});
  if (s) s.addEventListener("click", stopRead);
  if (saveBtn) saveBtn.addEventListener("click", persistReaderKeys);
  function onEnter(el) {{
    if (!el) return;
    el.addEventListener("keydown", function(ev) {{ if (ev.key === "Enter") {{ ev.preventDefault(); persistReaderKeys(); }} }});
  }}
  onEnter(keyInp);
  onEnter(regInp);
  var spdDn = document.getElementById("readerSpeedDown");
  var spdUp = document.getElementById("readerSpeedUp");
  if (spdDn) spdDn.addEventListener("click", function() {{ bumpSpeechRate(-RATE_STEP); }});
  if (spdUp) spdUp.addEventListener("click", function() {{ bumpSpeechRate(RATE_STEP); }});
  refreshSpeechRateUI();
  syncKeyHintOnLoad();
}})();
"""
