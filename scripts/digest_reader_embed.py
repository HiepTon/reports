"""Shared HTML/CSS/JS for on-page Read news: Gemini text model prepares lines, Gemini TTS speaks them."""

from __future__ import annotations

# Text prep for read-aloud (batched JSON array of strings); must support generateContent
READ_NEWS_SUMMARY_MODEL_DEFAULT = "gemini-3-flash-preview"
# Audio synthesis (Google AI generateContent + response modalities)
READ_NEWS_TTS_MODEL_DEFAULT = "gemini-3.1-flash-tts-preview"
READ_NEWS_VOICE_DEFAULT = "Enceladus"


def digest_reader_css() -> str:
    return """
    .reader-tools { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 0.75rem; width: 100%; margin-top: 0.65rem; padding-top: 0.65rem; border-top: 1px solid var(--border); }
    .reader-tools .read { background: var(--accent); color: #0a111a; }
    .reader-tools .stop { background: transparent; color: var(--muted); border: 1px solid var(--border); }
    .reader-tools .read:disabled, .reader-tools .stop:disabled { opacity: 0.45; cursor: not-allowed; }
    #readStatus { flex: 1 1 12rem; font-size: 0.8rem; color: var(--muted); margin: 0; min-height: 1.2em; }
    #readStatus.err { color: #f0a4a4; }
"""


def digest_reader_toolbar_inner(*, lang: str) -> str:
    if lang == "vi":
        return """<div class="reader-tools">
      <button type="button" class="apply read" id="readNews">Đọc tin</button>
      <button type="button" class="reset stop" id="stopRead" disabled>Dừng đọc</button>
      <p id="readStatus"></p>
    </div>"""
    return """<div class="reader-tools">
      <button type="button" class="apply read" id="readNews">Read news</button>
      <button type="button" class="reset stop" id="stopRead" disabled>Stop</button>
      <p id="readStatus"></p>
    </div>"""


def digest_reader_script(
    *,
    lang: str,
    summary_model: str,
    tts_model: str,
    voice: str,
) -> str:
    """Inline script; models/voice embedded as JSON strings."""
    import json

    sm = json.dumps(summary_model)
    tm = json.dumps(tts_model)
    vn = json.dumps(voice)
    l = json.dumps(lang)
    return f"""
(function() {{
  var LANG = {l};
  var SUMMARY_MODEL = {sm};
  var TTS_MODEL = {tm};
  var VOICE_NAME = {vn};
  try {{
    var sp = new URLSearchParams(window.location.search || "");
    var sm = sp.get("summaryModel");
    var tm = sp.get("ttsModel");
    var rv = sp.get("readerVoice");
    if (sm && String(sm).trim()) SUMMARY_MODEL = String(sm).trim();
    if (tm && String(tm).trim()) TTS_MODEL = String(tm).trim();
    if (rv && String(rv).trim()) VOICE_NAME = String(rv).trim();
  }} catch (e) {{}}
  var KEY = "reportsDigestReaderApiKey";
  var API_BASE = "https://generativelanguage.googleapis.com/v1beta/models/";
  var readAborted = false;
  var currentAudio = null;

  function status(msg, isErr) {{
    var el = document.getElementById("readStatus");
    if (!el) return;
    el.textContent = msg || "";
    el.className = isErr ? "err" : "";
  }}

  function getKey() {{
    try {{
      var k = sessionStorage.getItem(KEY);
      if (k) return k;
    }} catch (e) {{}}
    var p = window.prompt(
      LANG === "vi"
        ? "Dán Google AI Studio API key (chỉ lưu trong tab này, sessionStorage) để đọc tin:"
        : "Paste your Google AI Studio API key (stored in this tab only, sessionStorage) to read the news:"
    );
    if (!p) return "";
    p = p.trim();
    if (!p) return "";
    try {{ sessionStorage.setItem(KEY, p); }} catch (e) {{}}
    return p;
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

  async function callTts(apiKey, plainText) {{
    var langCode = LANG === "vi" ? "vi-VN" : "en-US";
    var prompt =
      LANG === "vi"
        ? "Đọc bản tin sau bằng giọng phát thanh viên trung lập, rõ ràng:\\n\\n" + plainText
        : "Read the following in a clear, neutral newscaster delivery:\\n\\n" + plainText;
    var url = API_BASE + encodeURIComponent(TTS_MODEL) + ":generateContent?key=" + encodeURIComponent(apiKey);
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
    var key = getKey();
    if (!key) {{
      status(LANG === "vi" ? "Thiếu API key." : "Missing API key.", true);
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
    var lines = [];
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
        var data = await callGenerateText(key, SUMMARY_MODEL, buildSummaryPrompt(batch));
        var out = extractText(data);
        var part = parseReaderJson(out);
        if (part.length !== batch.length) {{
          console.warn("Summary length mismatch", part.length, batch.length);
        }}
        for (var i = 0; i < batch.length; i++) {{
          var line = (part[i] || batch[i].title + ". " + batch[i].text).trim();
          if (line) lines.push(line);
        }}
      }}
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

    try {{
      for (var j = 0; j < lines.length; j++) {{
        if (readAborted) break;
        status(
          (LANG === "vi" ? "Đang tạo giọng (Gemini TTS)… " : "Synthesizing speech (Gemini TTS)… ") +
            (j + 1) +
            "/" +
            lines.length
        );
        var wav = await callTts(key, lines[j]);
        if (readAborted) break;
        await playWavBuffer(wav);
      }}
    }} catch (e) {{
      status(String(e && e.message ? e.message : e), true);
      if (readBtn) readBtn.disabled = false;
      if (stopBtn) stopBtn.disabled = true;
      return;
    }}

    status(readAborted ? (LANG === "vi" ? "Đã dừng." : "Stopped.") : LANG === "vi" ? "Đã đọc xong." : "Finished reading.", false);
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
  if (r) r.addEventListener("click", function() {{ runRead().catch(function(e) {{ status(String(e), true); }}); }});
  if (s) s.addEventListener("click", stopRead);
}})();
"""
