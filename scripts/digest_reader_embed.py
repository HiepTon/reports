"""Shared HTML/CSS/JS for on-page Read news: Gemini text model prepares lines, Gemini TTS speaks them."""

from __future__ import annotations

# Text prep for read-aloud (batched JSON array of strings); must support generateContent
READ_NEWS_SUMMARY_MODEL_DEFAULT = "gemini-3.1-flash-lite"
READ_NEWS_SUMMARY_FALLBACK_MODEL_DEFAULT = "gemini-2.5-flash-lite"
# Audio synthesis (Google AI generateContent + response modalities)
READ_NEWS_TTS_MODEL_DEFAULT = "gemini-3.1-flash-tts-preview"
READ_NEWS_TTS_FALLBACK_MODEL_DEFAULT = "gemini-2.5-flash-tts"
READ_NEWS_VOICE_DEFAULT = "Enceladus"


def digest_reader_css() -> str:
    return """
    .reader-tools { display: flex; flex-direction: column; gap: 0.55rem; width: 100%; margin-top: 0.65rem; padding-top: 0.65rem; border-top: 1px solid var(--border); }
    .reader-key-row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.45rem 0.65rem; width: 100%; font-size: 0.88rem; }
    .reader-key-row label { color: var(--muted); font-weight: 600; }
    .reader-api-input {
      flex: 1 1 14rem; min-width: 11rem; max-width: 100%; padding: 0.4rem 0.55rem; border-radius: 6px;
      border: 1px solid var(--border); background: var(--bg); color: var(--text); font-size: 0.88rem;
    }
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
        <label for="readerApiKeyInput">API key (Google AI Studio)</label>
        <input type="password" id="readerApiKeyInput" class="reader-api-input" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="Dán key và bấm Lưu — chỉ lưu trong phiên tab (sessionStorage)"/>
        <button type="button" class="reset reader-save-key" id="saveReaderApiKey">Lưu khóa</button>
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
        <label for="readerApiKeyInput">API key (Google AI Studio)</label>
        <input type="password" id="readerApiKeyInput" class="reader-api-input" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="Paste key and Save — stored in this tab only (sessionStorage)"/>
        <button type="button" class="reset reader-save-key" id="saveReaderApiKey">Save key</button>
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
    summary_model: str,
    tts_model: str,
    voice: str,
    summary_model_fallback: str | None = None,
    tts_model_fallback: str | None = None,
) -> str:
    """Inline script; models/voice embedded as JSON strings."""
    import json

    sm = json.dumps(summary_model)
    tm = json.dumps(tts_model)
    vn = json.dumps(voice)
    sm_fb = json.dumps(summary_model_fallback or READ_NEWS_SUMMARY_FALLBACK_MODEL_DEFAULT)
    tm_fb = json.dumps(tts_model_fallback or READ_NEWS_TTS_FALLBACK_MODEL_DEFAULT)
    l = json.dumps(lang)
    return f"""
(function() {{
  var LANG = {l};
  var SUMMARY_MODEL = {sm};
  var TTS_MODEL = {tm};
  var VOICE_NAME = {vn};
  var SUMMARY_FALLBACK = {sm_fb};
  var TTS_FALLBACK = {tm_fb};
  try {{
    var sp = new URLSearchParams(window.location.search || "");
    var sm = sp.get("summaryModel");
    var tm = sp.get("ttsModel");
    var rv = sp.get("readerVoice");
    var smf = sp.get("summaryFallbackModel");
    var tmf = sp.get("ttsFallbackModel");
    if (sm && String(sm).trim()) SUMMARY_MODEL = String(sm).trim();
    if (tm && String(tm).trim()) TTS_MODEL = String(tm).trim();
    if (rv && String(rv).trim()) VOICE_NAME = String(rv).trim();
    if (smf && String(smf).trim()) SUMMARY_FALLBACK = String(smf).trim();
    if (tmf && String(tmf).trim()) TTS_FALLBACK = String(tmf).trim();
  }} catch (e) {{}}
  var KEY = "reportsDigestReaderApiKey";
  var API_BASE = "https://generativelanguage.googleapis.com/v1beta/models/";
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
    try {{
      sessionStorage.setItem(RATE_STORAGE_KEY, String(r));
    }} catch (e) {{}}
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
    if (fromQs != null) {{
      speechRateCached = persistSpeechRate(fromQs);
      return;
    }}
    try {{
      var raw = sessionStorage.getItem(RATE_STORAGE_KEY);
      if (raw != null && String(raw).trim() !== "") {{
        var v = parseFloat(String(raw).replace(",", "."));
        if (isFinite(v)) {{
          speechRateCached = clampSpeechRate(v);
          return;
        }}
      }}
    }} catch (e) {{}}
    speechRateCached = 1;
  }}

  function getSpeechRate() {{
    return speechRateCached;
  }}

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
      try {{
        currentAudio.playbackRate = getSpeechRate();
      }} catch (e) {{}}
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
    if (!el) return;
    el.textContent = msg || "";
  }}

  function readStoredKey() {{
    try {{
      var k = sessionStorage.getItem(KEY);
      return k ? String(k).trim() : "";
    }} catch (e) {{
      return "";
    }}
  }}

  function persistApiKey(raw) {{
    var k = String(raw || "").trim();
    if (!k) {{
      readKeyHint(LANG === "vi" ? "Key trống." : "Key is empty.");
      return false;
    }}
    try {{
      sessionStorage.setItem(KEY, k);
    }} catch (e) {{
      readKeyHint(LANG === "vi" ? "Không lưu được (sessionStorage)." : "Could not save (sessionStorage).");
      return false;
    }}
    readKeyHint(LANG === "vi" ? "Đã lưu trong phiên tab." : "Saved for this tab.");
    return true;
  }}

  function getApiKeyForRead() {{
    var stored = readStoredKey();
    if (stored) return stored;
    var inp = document.getElementById("readerApiKeyInput");
    return inp ? String(inp.value || "").trim() : "";
  }}

  function syncKeyHintOnLoad() {{
    if (readStoredKey()) {{
      readKeyHint(LANG === "vi" ? "Đã có khóa trong phiên tab." : "Key saved for this tab.");
    }}
  }}

  function saveReaderApiKeyClick() {{
    var inp = document.getElementById("readerApiKeyInput");
    var v = inp ? inp.value : "";
    if (persistApiKey(v) && inp) {{
      inp.value = "";
    }}
  }}

  function visibleCards() {{
    return Array.prototype.slice.call(document.querySelectorAll("article.card")).filter(function(c) {{ return !c.hidden; }});
  }}

  function cardPayload(card) {{
    var ta = card.querySelector(".topic a");
    var title = ta ? ta.textContent.trim() : "";
    var blocks = card.querySelectorAll(".block p");
    var chunks = [];
    for (var i = 0; i < blocks.length; i++) chunks.push(blocks[i].textContent.trim());
    return {{ title: title, text: chunks.join(" ") }};
  }}

  function extractText(data) {{
    try {{
      var parts = data.candidates[0].content.parts;
      return parts.map(function(p) {{ return p.text || ""; }}).join("");
    }} catch (e) {{
      return "";
    }}
  }}

  function stripJsonFence(s) {{
    s = (s || "").trim();
    if (s.indexOf("```") === 0) {{
      s = s.replace(/^```[a-zA-Z]*\\n?/, "").replace(/```\\s*$/, "").trim();
    }}
    return s;
  }}

  function parseReaderJson(raw) {{
    var t = stripJsonFence(raw);
    var arr = JSON.parse(t);
    if (!Array.isArray(arr)) throw new Error("Summary model did not return a JSON array.");
    return arr.map(function(x) {{ return String(x || "").trim(); }}).filter(Boolean);
  }}

  function buildSummaryPrompt(batch) {{
    if (LANG === "vi") {{
      return (
        "Bạn là biên tập phát thanh. Nhận mảng JSON các bài (title, text). "
        + "Trả về DUY NHẤT một mảng JSON các chuỗi, cùng độ dài và thứ tự. Mỗi chuỗi là 2–4 câu tiếng Việt, tông đọc báo trung lập, sẵn sàng chuyển thành giọng nói; không markdown; escape JSON đúng chuẩn.\\n\\n"
        + "INPUT_JSON:\\n"
        + JSON.stringify(batch)
      );
    }}
    return (
      "You are a broadcast editor. Input is a JSON array of items with title and text. "
      + "Return ONLY a JSON array of strings, same length and order. Each string is 2–4 sentences, neutral newsreader tone, suitable for speech synthesis; no markdown; valid JSON escaping.\\n\\n"
      + "INPUT_JSON:\\n"
      + JSON.stringify(batch)
    );
  }}

  async function callGenerateTextWithFallback(apiKey, userText) {{
    try {{
      return await callGenerateText(apiKey, SUMMARY_MODEL, userText);
    }} catch (e1) {{
      if (!SUMMARY_FALLBACK || SUMMARY_FALLBACK === SUMMARY_MODEL) throw e1;
      console.warn("Read-news summary: primary model failed, trying fallback", e1);
      return await callGenerateText(apiKey, SUMMARY_FALLBACK, userText);
    }}
  }}

  async function callGenerateText(apiKey, modelId, userText) {{
    var url = API_BASE + encodeURIComponent(modelId) + ":generateContent?key=" + encodeURIComponent(apiKey);
    var body = {{
      contents: [{{ role: "user", parts: [{{ text: userText }}] }}],
      generationConfig: {{ temperature: 0.35, maxOutputTokens: 8192 }},
    }};
    var res = await fetch(url, {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify(body),
    }});
    var raw = await res.text();
    if (!res.ok) throw new Error(raw || res.statusText);
    return JSON.parse(raw);
  }}

  function extractInlineAudio(data) {{
    try {{
      var parts = data.candidates[0].content.parts;
      for (var i = 0; i < parts.length; i++) {{
        var p = parts[i];
        var id = p.inlineData || p.inline_data;
        if (id && id.data) return id;
      }}
    }} catch (e) {{}}
    return null;
  }}

  function b64ToBytes(b64) {{
    var bin = atob(b64);
    var u = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
    return u;
  }}

  function pcm16MonoToWav(pcmBytes, sampleRate) {{
    var numChannels = 1;
    var bitsPerSample = 16;
    var blockAlign = numChannels * bitsPerSample / 8;
    var byteRate = sampleRate * blockAlign;
    var dataSize = pcmBytes.byteLength;
    var out = new ArrayBuffer(44 + dataSize);
    var view = new DataView(out);
    function wstr(off, s) {{
      for (var j = 0; j < s.length; j++) view.setUint8(off + j, s.charCodeAt(j));
    }}
    wstr(0, "RIFF");
    view.setUint32(4, 36 + dataSize, true);
    wstr(8, "WAVE");
    wstr(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, numChannels, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, byteRate, true);
    view.setUint16(32, blockAlign, true);
    view.setUint16(34, bitsPerSample, true);
    wstr(36, "data");
    view.setUint32(40, dataSize, true);
    new Uint8Array(out, 44).set(pcmBytes);
    return out;
  }}

  function truncateTts(text, maxLen) {{
    if (text.length <= maxLen) return text;
    return text.slice(0, maxLen - 1).replace(/\\s+\\S*$/, "") + "…";
  }}

  async function callTtsOneModel(apiKey, plainText, modelId) {{
    var langCode = LANG === "vi" ? "vi-VN" : "en-US";
    var prompt =
      LANG === "vi"
        ? "Đọc bản tin sau bằng giọng phát thanh viên trung lập, rõ ràng:\\n\\n" + plainText
        : "Read the following in a clear, neutral newscaster delivery:\\n\\n" + plainText;
    var url = API_BASE + encodeURIComponent(modelId) + ":generateContent?key=" + encodeURIComponent(apiKey);
    var body = {{
      contents: [{{ role: "user", parts: [{{ text: truncateTts(prompt, 8000) }}] }}],
      generationConfig: {{
        responseModalities: ["AUDIO"],
        speechConfig: {{
          languageCode: langCode,
          voiceConfig: {{
            prebuiltVoiceConfig: {{
              voiceName: VOICE_NAME,
            }},
          }},
        }},
        temperature: 1.2,
      }},
    }};
    var res = await fetch(url, {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify(body),
    }});
    var raw = await res.text();
    if (!res.ok) throw new Error(raw || res.statusText);
    var data = JSON.parse(raw);
    var id = extractInlineAudio(data);
    if (!id || !id.data) throw new Error("TTS response missing inline audio data.");
    var mime = (id.mimeType || id.mime_type || "").toLowerCase();
    var pcm = b64ToBytes(id.data);
    var rate = 24000;
    var m = /rate=(\\d+)/i.exec(mime);
    if (m) rate = parseInt(m[1], 10) || 24000;
    if (mime.indexOf("l16") >= 0 || mime.indexOf("pcm") >= 0 || mime.indexOf("raw") >= 0) {{
      return pcm16MonoToWav(pcm, rate);
    }}
    throw new Error("Unsupported TTS mime type: " + mime);
  }}

  async function callTts(apiKey, plainText) {{
    try {{
      return await callTtsOneModel(apiKey, plainText, TTS_MODEL);
    }} catch (e1) {{
      if (!TTS_FALLBACK || TTS_FALLBACK === TTS_MODEL) throw e1;
      console.warn("Read-news TTS: primary model failed, trying fallback", e1);
      return await callTtsOneModel(apiKey, plainText, TTS_FALLBACK);
    }}
  }}

  function playWavBuffer(buf) {{
    return new Promise(function(resolve, reject) {{
      if (readAborted) {{
        resolve();
        return;
      }}
      var blob = new Blob([buf], {{ type: "audio/wav" }});
      var url = URL.createObjectURL(blob);
      var audio = new Audio(url);
      currentAudio = audio;
      try {{
        audio.playbackRate = getSpeechRate();
      }} catch (e) {{}}
      audio.onended = function() {{
        URL.revokeObjectURL(url);
        currentAudio = null;
        resolve();
      }};
      audio.onerror = function() {{
        URL.revokeObjectURL(url);
        currentAudio = null;
        reject(new Error("Audio playback failed."));
      }};
      audio.play().catch(function(e) {{
        URL.revokeObjectURL(url);
        currentAudio = null;
        reject(e);
      }});
    }});
  }}

  async function runRead() {{
    readAborted = false;
    var cards = visibleCards();
    if (!cards.length) {{
      status(LANG === "vi" ? "Không có bài đang hiển thị." : "No articles visible.", true);
      return;
    }}
    var key = getApiKeyForRead();
    if (!key) {{
      status(
        LANG === "vi"
          ? "Nhập API key vào ô trên, bấm Lưu khóa (hoặc để key trong ô rồi bấm Đọc tin)."
          : "Enter your API key above and Save key (or leave it in the field and click Read news).",
        true
      );
      var inp = document.getElementById("readerApiKeyInput");
      if (inp) inp.focus();
      return;
    }}

    var readBtn = document.getElementById("readNews");
    var stopBtn = document.getElementById("stopRead");
    if (readBtn) readBtn.disabled = true;
    if (stopBtn) stopBtn.disabled = false;
    if (currentAudio) {{
      try {{ currentAudio.pause(); }} catch (e) {{}}
      currentAudio = null;
    }}

    var payloads = cards.map(cardPayload);
    var chunkSize = 8;
    var speakLines = [];
    var speakWaiters = [];
    var summaryFinished = false;
    var summaryFailed = null;

    function notifySpeakWaiters() {{
      var w = speakWaiters.slice();
      speakWaiters.length = 0;
      for (var wi = 0; wi < w.length; wi++) {{
        try {{
          w[wi]();
        }} catch (e) {{}}
      }}
    }}

    function appendSpeakLine(line) {{
      var t = String(line || "").trim();
      if (!t) return;
      speakLines.push(t);
      notifySpeakWaiters();
    }}

    async function waitSpeakLine(index) {{
      while (true) {{
        if (summaryFailed) throw summaryFailed;
        if (speakLines.length > index) return speakLines[index];
        if (summaryFinished && speakLines.length <= index) return null;
        await new Promise(function(resolve) {{
          speakWaiters.push(resolve);
        }});
      }}
    }}

    async function summarizeTask() {{
      try {{
        for (var c = 0; c < payloads.length; c += chunkSize) {{
          if (readAborted) break;
          var batch = payloads.slice(c, c + chunkSize);
          status(
            (LANG === "vi" ? "Đang soạn lời (Gemini)… " : "Summarizing for speech (Gemini)… ") +
              (c + 1) +
              "–" +
              Math.min(c + batch.length, payloads.length) +
              "/" +
              payloads.length
          );
          var data = await callGenerateTextWithFallback(key, buildSummaryPrompt(batch));
          var out = extractText(data);
          var part = parseReaderJson(out);
          if (part.length !== batch.length) {{
            console.warn("Summary length mismatch", part.length, batch.length);
          }}
          for (var i = 0; i < batch.length; i++) {{
            var line = (part[i] || batch[i].title + ". " + batch[i].text).trim();
            appendSpeakLine(line);
          }}
        }}
      }} catch (e) {{
        summaryFailed = e;
      }} finally {{
        summaryFinished = true;
        notifySpeakWaiters();
      }}
    }}

    async function speakTask() {{
      var line0 = await waitSpeakLine(0);
      if (!line0 || readAborted) return;
      var prefetch = callTts(key, line0);
      for (var j = 0; ; j++) {{
        if (readAborted) break;
        status((LANG === "vi" ? "Đang đọc… " : "Playing… ") + (j + 1));
        var wav = await prefetch;
        if (readAborted) break;
        var nextLine = await waitSpeakLine(j + 1);
        if (nextLine) {{
          prefetch = callTts(key, nextLine);
        }} else {{
          prefetch = Promise.resolve(null);
        }}
        await playWavBuffer(wav);
        if (!nextLine) break;
      }}
    }}

    try {{
      await Promise.all([summarizeTask(), speakTask()]);
      if (summaryFailed) throw summaryFailed;
    }} catch (e) {{
      status(String(e && e.message ? e.message : e), true);
      if (readBtn) readBtn.disabled = false;
      if (stopBtn) stopBtn.disabled = true;
      return;
    }}

    if (readAborted) {{
      status(LANG === "vi" ? "Đã hủy." : "Cancelled.", false);
      if (readBtn) readBtn.disabled = false;
      if (stopBtn) stopBtn.disabled = true;
      return;
    }}

    status(LANG === "vi" ? "Đã đọc xong." : "Finished reading.", false);
    if (readBtn) readBtn.disabled = false;
    if (stopBtn) stopBtn.disabled = true;
  }}

  function stopRead() {{
    readAborted = true;
    if (currentAudio) {{
      try {{
        currentAudio.pause();
        currentAudio.removeAttribute("src");
        currentAudio.load();
      }} catch (e) {{}}
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
  var keyInput = document.getElementById("readerApiKeyInput");
  if (r) r.addEventListener("click", function() {{ runRead().catch(function(e) {{ status(String(e), true); }}); }});
  if (s) s.addEventListener("click", stopRead);
  if (saveBtn) saveBtn.addEventListener("click", saveReaderApiKeyClick);
  if (keyInput) {{
    keyInput.addEventListener("keydown", function(ev) {{
      if (ev.key === "Enter") {{
        ev.preventDefault();
        saveReaderApiKeyClick();
      }}
    }});
  }}
  var spdDn = document.getElementById("readerSpeedDown");
  var spdUp = document.getElementById("readerSpeedUp");
  if (spdDn) spdDn.addEventListener("click", function() {{ bumpSpeechRate(-RATE_STEP); }});
  if (spdUp) spdUp.addEventListener("click", function() {{ bumpSpeechRate(RATE_STEP); }});
  refreshSpeechRateUI();
  syncKeyHintOnLoad();
}})();
"""
