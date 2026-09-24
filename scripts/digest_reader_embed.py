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

# Pinned official browser bundle on jsdelivr (a public CDN). We prefer this over
# the aka.ms redirector because some browsers' built-in ad/tracker blockers and
# VPNs (notably Opera) block aka.ms and Microsoft CDN hosts, which left
# window.SpeechSDK undefined. aka.ms is kept as a runtime fallback.
AZURE_SPEECH_SDK_URL = (
    "https://cdn.jsdelivr.net/npm/microsoft-cognitiveservices-speech-sdk@1.51.0"
    "/distrib/browser/microsoft.cognitiveservices.speech.sdk.bundle-min.js"
)
AZURE_SPEECH_SDK_URL_FALLBACK = "https://aka.ms/csspeech/jsbrowserpackageraw"


def digest_reader_sdk_script_tag() -> str:
    """<script> tags that load the Azure Speech SDK browser bundle (window.SpeechSDK).

    If the primary CDN is blocked, the onerror handler injects the aka.ms
    redirector as a fallback so window.SpeechSDK can still be defined. If that
    also fails we set window.__readerSdkBlocked so the reader can show a clearer
    "your browser is blocking it" hint.
    """
    fallback_loader = (
        "var s=document.createElement('script');"
        "s.src='" + AZURE_SPEECH_SDK_URL_FALLBACK + "';"
        "s.onerror=function(){window.__readerSdkBlocked=true;};"
        "document.head.appendChild(s);"
    )
    return (
        '<script src="' + AZURE_SPEECH_SDK_URL + '" '
        'onerror="' + fallback_loader + '"></script>'
    )


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
  var READ_LABEL = LANG === "vi" ? "Đọc tin" : "Read news";
  var resumeIndex = 0;   // where the next "Read" starts (0 = beginning; set after an error/stop)
  var currentIndex = 0;  // item currently being read (for resume + status)
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
  function cardTitle(card) {{
    var ta = card.querySelector(".topic a");
    return ta ? ta.textContent.trim() : "";
  }}
  function cardText(card) {{
    var title = cardTitle(card);
    var blocks = card.querySelectorAll(".block p");
    var chunks = [];
    for (var i = 0; i < blocks.length; i++) chunks.push(blocks[i].textContent.trim());
    return (title ? title + ". " : "") + chunks.join(" ");
  }}

  // One synthesizer per voice, kept alive across cards so the Azure websocket
  // stays warm instead of reconnecting per card (per-card reconnects were a
  // source of wss 1006). Disposed on error (so a retry reconnects) and when the
  // read finishes/stops or the credentials change.
  var synthCache = {{}};   // voiceId -> SpeechSynthesizer
  var synthCreds = null;   // {{ key, region }} the cached synthesizers were built with

  function disposeSynth(voiceId) {{
    var s = synthCache[voiceId];
    if (s) {{ try {{ s.close(); }} catch (e) {{}} delete synthCache[voiceId]; }}
  }}
  function disposeAllSynths() {{
    for (var k in synthCache) {{ if (Object.prototype.hasOwnProperty.call(synthCache, k)) disposeSynth(k); }}
    synthCache = {{}};
    synthCreds = null;
  }}
  function getSynth(SDK, key, region, voiceId) {{
    if (!synthCreds || synthCreds.key !== key || synthCreds.region !== region) {{
      disposeAllSynths();
      synthCreds = {{ key: key, region: region }};
    }}
    if (!synthCache[voiceId]) {{
      var cfg = SDK.SpeechConfig.fromSubscription(key, region);
      cfg.speechSynthesisVoiceName = voiceId;
      cfg.speechSynthesisOutputFormat = SDK.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3;
      synthCache[voiceId] = new SDK.SpeechSynthesizer(cfg, null);
    }}
    return synthCache[voiceId];
  }}

  function synthOnce(key, region, text, voiceId) {{
    return new Promise(function(resolve, reject) {{
      var SDK = window.SpeechSDK;
      if (!SDK) {{
        var blocked = !!window.__readerSdkBlocked;
        reject(new Error(
          LANG === "vi"
            ? (blocked
                ? "Chưa tải được Azure Speech SDK — trình duyệt đang chặn (tắt trình chặn quảng cáo/VPN của Opera hoặc dùng trình duyệt khác)."
                : "Chưa tải được Azure Speech SDK — thử tải lại trang.")
            : (blocked
                ? "Azure Speech SDK failed to load — your browser is blocking it (disable Opera's built-in ad blocker/VPN, or use another browser)."
                : "Azure Speech SDK failed to load — try reloading the page.")
        ));
        return;
      }}
      var synth;
      try {{
        synth = getSynth(SDK, key, region, voiceId);
      }} catch (e) {{ disposeSynth(voiceId); reject(e); return; }}
      synth.speakTextAsync(
        text,
        function(result) {{
          if (result.reason === SDK.ResultReason.SynthesizingAudioCompleted) {{
            resolve(result.audioData);  // keep the synthesizer open for the next card
          }} else {{
            var msg = result.errorDetails || ("TTS failed: " + result.reason);
            disposeSynth(voiceId);      // drop it so a retry rebuilds the connection
            reject(new Error(msg));
          }}
        }},
        function(err) {{ disposeSynth(voiceId); reject(new Error(String(err))); }}
      );
    }});
  }}
  // Ignore a pending prefetch we no longer await, so closing its synthesizer
  // does not surface as an unhandled promise rejection.
  function swallow(p) {{ if (p && typeof p.then === "function") {{ try {{ p.then(null, function() {{}}); }} catch (e) {{}} }} }}
  function sleep(ms) {{ return new Promise(function(r) {{ setTimeout(r, ms); }}); }}
  function isTransientErr(e) {{
    var m = (e && e.message ? e.message : String(e)) || "";
    // 1006 = abnormal websocket close; also generic connect/timeout/network drops.
    return /1006|Unable to contact server|websocket|connection\\s|connection\\.|timed out|timeout|network/i.test(m);
  }}
  // Retry the SAME voice on transient connection drops (e.g. wss 1006) before giving up.
  // Backoff grows (1s, 2s, 4s, 6s) to ride through short Azure outages rather than aborting.
  var SYNTH_RETRIES = 5;
  var SYNTH_BACKOFF_MS = [1000, 2000, 4000, 6000];
  async function synthWithRetry(key, region, text, voiceId) {{
    var lastErr;
    for (var attempt = 0; attempt < SYNTH_RETRIES; attempt++) {{
      if (readAborted) throw new Error("aborted");
      try {{
        return await synthOnce(key, region, text, voiceId);
      }} catch (e) {{
        lastErr = e;
        if (!isTransientErr(e) || attempt === SYNTH_RETRIES - 1) throw e;
        console.warn("Reader TTS: transient failure, retrying (" + (attempt + 1) + ")", e);
        await sleep(SYNTH_BACKOFF_MS[Math.min(attempt, SYNTH_BACKOFF_MS.length - 1)]);
      }}
    }}
    throw lastErr;
  }}
  async function synth(key, region, text) {{
    try {{
      return await synthWithRetry(key, region, text, VOICE);
    }} catch (e1) {{
      if (!VOICE_FALLBACK || VOICE_FALLBACK === VOICE) throw e1;
      console.warn("Reader TTS: primary voice failed, trying fallback", e1);
      return await synthWithRetry(key, region, text, VOICE_FALLBACK);
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
      // A stop() tears down the element and fires 'error'; that's expected, not a failure.
      audio.onerror = function() {{ URL.revokeObjectURL(url); currentAudio = null; if (readAborted) {{ resolve(); }} else {{ reject(new Error("Audio playback failed.")); }} }};
      audio.play().catch(function(e) {{ URL.revokeObjectURL(url); currentAudio = null; if (readAborted) {{ resolve(); }} else {{ reject(e); }} }});
    }});
  }}

  function setReadingUI(reading) {{
    var readBtn = document.getElementById("readNews");
    var stopBtn = document.getElementById("stopRead");
    if (readBtn) readBtn.disabled = reading;
    if (stopBtn) stopBtn.disabled = !reading;
  }}
  function updateReadButtonLabel() {{
    var readBtn = document.getElementById("readNews");
    if (!readBtn) return;
    readBtn.textContent = resumeIndex > 0
      ? (LANG === "vi" ? "Đọc tiếp (mục " : "Continue (item ") + (resumeIndex + 1) + ")"
      : READ_LABEL;
  }}
  function itemLabel(j, total, title) {{
    var t = title ? ": " + (title.length > 80 ? title.slice(0, 79) + "…" : title) : "";
    return (j + 1) + "/" + total + t;
  }}

  async function runRead(startIndex) {{
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

    var texts = [];
    var titles = [];
    for (var c = 0; c < cards.length; c++) {{
      var tx = cardText(cards[c]);
      if (tx && tx.trim()) {{ texts.push(tx); titles.push(cardTitle(cards[c])); }}
    }}
    var total = texts.length;
    if (!total) {{
      status(LANG === "vi" ? "Không có nội dung để đọc." : "No readable content.", true);
      return;
    }}
    var start = Math.min(Math.max(parseInt(startIndex, 10) || 0, 0), total - 1);

    setReadingUI(true);
    if (currentAudio) {{ try {{ currentAudio.pause(); }} catch (e) {{}} currentAudio = null; }}

    var prefetch = null;
    var j = start;
    try {{
      prefetch = synth(key, region, texts[start]);
      for (j = start; j < total; j++) {{
        if (readAborted) break;
        currentIndex = j;
        status((LANG === "vi" ? "Đang đọc " : "Reading ") + itemLabel(j, total, titles[j]));
        var buf = await prefetch;
        if (readAborted) break;
        prefetch = (j + 1 < total) ? synth(key, region, texts[j + 1]) : null;
        await playMp3Buffer(buf);
      }}
    }} catch (e) {{
      swallow(prefetch); disposeAllSynths();
      if (readAborted) {{
        resumeIndex = currentIndex;
        status((LANG === "vi" ? "Đã dừng ở mục " : "Stopped at item ") + itemLabel(currentIndex, total, titles[currentIndex]) +
          (LANG === "vi" ? " — bấm Đọc tiếp để tiếp tục." : " — click Continue to resume."), false);
      }} else {{
        resumeIndex = j;  // remember the failed item so the user can retry from here
        status((LANG === "vi" ? "Lỗi ở mục " : "Error at item ") + itemLabel(j, total, titles[j]) + " — " +
          String(e && e.message ? e.message : e) + " " +
          (LANG === "vi" ? "Bấm \\u201cĐọc tiếp\\u201d để đọc lại từ mục này." : "Click \\u201cContinue\\u201d to retry from this item."), true);
      }}
      setReadingUI(false);
      updateReadButtonLabel();
      return;
    }}

    swallow(prefetch); disposeAllSynths();
    if (readAborted) {{
      resumeIndex = currentIndex;  // resume from where the user stopped
      status((LANG === "vi" ? "Đã dừng ở mục " : "Stopped at item ") + itemLabel(currentIndex, total, titles[currentIndex]) +
        (LANG === "vi" ? " — bấm Đọc tiếp để tiếp tục." : " — click Continue to resume."), false);
    }} else {{
      resumeIndex = 0;  // finished the whole list — next read starts from the top
      status(LANG === "vi" ? "Đã đọc xong." : "Finished reading.", false);
    }}
    setReadingUI(false);
    updateReadButtonLabel();
  }}

  function stopRead() {{
    // Just signal the abort + tear down audio; runRead's loop reports the stop point,
    // sets resumeIndex, and restores the buttons (so "Đọc tiếp" resumes from here).
    readAborted = true;
    if (currentAudio) {{
      try {{ currentAudio.pause(); currentAudio.removeAttribute("src"); currentAudio.load(); }} catch (e) {{}}
      currentAudio = null;
    }}
    disposeAllSynths();  // free the Azure websocket immediately on stop
  }}

  var r = document.getElementById("readNews");
  var s = document.getElementById("stopRead");
  var saveBtn = document.getElementById("saveReaderApiKey");
  var keyInp = document.getElementById("readerAzureKeyInput");
  var regInp = document.getElementById("readerAzureRegionInput");
  if (r) r.addEventListener("click", function() {{ runRead(resumeIndex).catch(function(e) {{ status(String(e), true); }}); }});
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
