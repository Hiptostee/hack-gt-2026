"use strict";
const $ = id => document.getElementById(id);
let token = "", scene = null, locationFix = null, lastLandmark = null;
let answer = "", busy = false, pendingAction = null, locatorContext = null, locatorTimer;
let helpHold, held = false, recognition;
const notice = text => { $("notice").textContent = text; };
function saved(key) { try { return localStorage.getItem(key); } catch { return null; } }
function save(key, value) { try { localStorage.setItem(key, value); return true; } catch { return false; } }
$("contact").value = saved("companion-contact") || "";
try { lastLandmark = JSON.parse(saved("companion-landmark")); } catch { /* No saved landmark. */ }

async function api(path, data) {
  const response = await fetch(path, {
    method: data === undefined ? "GET" : "POST",
    headers: { "Authorization": `Bearer ${token}`, "Content-Type": "application/json" },
    body: data === undefined ? undefined : JSON.stringify(data),
    signal: AbortSignal.timeout(35000)
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Request failed.");
  return result;
}
async function guarded(action) {
  try { await action(); }
  catch (error) { notice(error.name === "TimeoutError" ? "Request timed out. Try again." : error.message); }
}
function setBusy(value) {
  busy = value;
  document.querySelectorAll("[data-scene]").forEach(el => { el.disabled = value; });
}
async function sceneTask(action) {
  if (busy) return;
  setBusy(true);
  try { await guarded(action); } finally { setBusy(false); }
}
function requireScene() {
  if (!scene) throw new Error("Capture or select a scene first.");
  if (Date.now() - scene.selectedAt > scene.expires_in * 1000) throw new Error("This scene expired. Capture or select another image.");
  return scene;
}
function sceneLabel(s = scene) {
  if (!s) return "No image selected.";
  return s.captured_at ? `Wearable image captured ${new Date(s.captured_at * 1000).toLocaleTimeString()}.`
    : "Selected photo. Its original capture time is unknown.";
}
async function useScene(result) {
  const old = scene;
  scene = { ...result, selectedAt: Date.now() };
  $("scene-image").src = result.data_url;
  $("scene-age").textContent = sceneLabel();
  $("preview").hidden = false;
  answer = "";
  $("answer").textContent = "Image ready. Describe it, read text, or ask a question.";
  if (old) await api("/api/forget", { snapshot_id: old.id }).catch(() => {});
  notice("Image ready. It is sent to Gemini only when you request an answer.");
}
async function ask(mode, question = "") {
  const current = requireScene();
  notice("Reading this image. Help controls remain available.");
  const result = await api("/api/ask", { snapshot_id: current.id, mode, question });
  if (scene?.id !== result.snapshot_id) return;
  answer = `${sceneLabel(current)}\n${result.answer}`;
  $("answer").textContent = answer;
  if (result.landmark) {
    lastLandmark = { label: result.landmark, observed: current.captured_at,
      identified: Date.now(), source: current.source };
    save("companion-landmark", JSON.stringify(lastLandmark));
    updateLandmark();
  }
  notice("Answer ready. Follow-up questions use this same image.");
}
function speak(text) {
  if (!("speechSynthesis" in window)) return notice("Speech output is unavailable in this browser.");
  speechSynthesis.cancel();
  speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}
function updateLandmark() {
  let text = "No landmark has been observed yet.";
  if (lastLandmark && typeof lastLandmark.label === "string") {
    const when = lastLandmark.observed ? `in an image captured ${new Date(lastLandmark.observed * 1000).toLocaleString()}`
      : "in a selected photo whose capture time is unknown";
    text = `Last image landmark: ${lastLandmark.label}, ${when}. This is not your current location.`;
  }
  $("landmark").textContent = text;
}
async function refreshStatus() {
  const result = await api("/api/status");
  $("device-status").textContent = `Wearable reachable. Camera: ${result.camera}. Gemini: ${result.gemini_configured ? result.cloud : "API key not configured"}. Pi battery: ${result.pi_battery}.`;
}
$("connect").onclick = () => guarded(async () => {
  token = $("token").value.trim();
  await refreshStatus(); $("connection").open = false; notice("Connected to the wearable.");
});
$("refresh").onclick = () => guarded(refreshStatus);
$("capture").onclick = () => sceneTask(async () => useScene(await api("/api/snapshot", {})));
$("photo").onchange = () => sceneTask(async () => {
  const file = $("photo").files[0];
  if (!file) return;
  if (file.size > 20 * 1024 * 1024) throw new Error("Choose a photo smaller than 20 MB.");
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale); canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height); bitmap.close();
  const encoded = canvas.toDataURL("image/jpeg", .9).split(",")[1];
  await useScene(await api("/api/upload", { image: encoded, mime: "image/jpeg" }));
  $("photo").value = "";
});
$("describe").onclick = () => sceneTask(() => ask("describe"));
$("read").onclick = () => sceneTask(() => ask("read"));
$("question-form").onsubmit = event => { event.preventDefault(); sceneTask(() => ask("ask", $("question").value.trim())); };
document.querySelectorAll("[data-question]").forEach(button => {
  button.onclick = () => { $("question").value = button.dataset.question; sceneTask(() => ask("ask", button.dataset.question)); };
});
$("speak").onclick = () => speak(answer || "No answer yet.");
$("stop-speech").onclick = () => window.speechSynthesis?.cancel();
$("forget").onclick = () => sceneTask(async () => {
  if (scene) await api("/api/forget", { snapshot_id: scene.id });
  scene = null; answer = ""; $("scene-image").removeAttribute("src"); $("preview").hidden = true;
  $("answer").textContent = "Scene and its conversation deleted."; window.speechSynthesis?.cancel();
  notice("Scene deleted. Saved landmark can be removed with Forget saved number and landmark.");
});

function confirmAction(message, label, action) {
  pendingAction = action; $("confirm-message").textContent = message;
  $("confirm-action").textContent = label; $("confirm-dialog").showModal();
}
$("cancel-action").onclick = () => { pendingAction = null; $("confirm-dialog").close(); };
$("confirm-dialog").addEventListener("cancel", () => { pendingAction = null; });
$("confirm-action").onclick = () => {
  const action = pendingAction; pendingAction = null; $("confirm-dialog").close();
  if (action) guarded(action); // Invoke synchronously to preserve native-share user activation.
};
function openHelp() { $("help-controls").hidden = false; $("help-focus").focus(); notice("Help controls open. Choose an action."); }
const cancelHold = () => clearTimeout(helpHold);
$("help-open").onpointerdown = event => {
  if (event.button !== 0) return;
  held = false;
  helpHold = setTimeout(() => { held = true; openHelp(); }, 2000);
};
["pointerup", "pointercancel", "pointerleave"].forEach(name => $("help-open").addEventListener(name, cancelHold));
window.addEventListener("blur", cancelHold);
$("help-open").onclick = () => {
  if (held) { held = false; return; }
  confirmAction("Open help controls? No one will be contacted until you choose and confirm an action.", "Open help controls", openHelp);
};
function phoneNumber() {
  const number = $("contact").value.trim();
  if (!/^\+?[0-9 ()-]{5,25}$/.test(number) || number.replace(/\D/g, "").length < 5)
    throw new Error("Enter your trusted person’s phone number, including country code.");
  return number.replace(/[ ()-]/g, "");
}
$("save-contact").onclick = () => guarded(() => {
  const phone = phoneNumber();
  notice(save("companion-contact", phone) ? "Number saved on this device." : "Storage unavailable; the number remains available in this tab.");
});
$("call").onclick = () => guarded(() => {
  const phone = phoneNumber();
  confirmAction(`Open your phone’s calling screen for ${phone}?`, "Open calling screen", () => {
    window.location.href = `tel:${phone}`; notice("Calling screen requested. Complete the call on your phone.");
  });
});
$("locate").onclick = () => {
  if (!isSecureContext || !navigator.geolocation) return notice("Phone location requires a trusted HTTPS connection and a supported browser.");
  locationFix = null; $("location-status").textContent = "Requesting phone location…";
  navigator.geolocation.getCurrentPosition(position => {
    locationFix = { lat: position.coords.latitude, lon: position.coords.longitude,
      accuracy: position.coords.accuracy, timestamp: position.timestamp };
    $("location-status").textContent = `Location obtained ${new Date(position.timestamp).toLocaleTimeString()}, accuracy approximately ${Math.round(position.coords.accuracy)} meters. It stays on this phone until you share.`;
  }, () => { $("location-status").textContent = "Location unavailable or permission denied. You can still call your trusted person."; },
  { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 });
};
function locationMessage() {
  if (!locationFix || Date.now() - locationFix.timestamp > 120000) throw new Error("Get a fresh phone location before sharing.");
  return `I need help finding my way. My phone location at ${new Date(locationFix.timestamp).toLocaleString()} (accuracy about ${Math.round(locationFix.accuracy)} m): https://maps.google.com/?q=${locationFix.lat},${locationFix.lon}`;
}
$("send-location").onclick = () => guarded(() => {
  const phone = phoneNumber(), text = locationMessage();
  confirmAction(`Open a text message to ${phone} containing your location? ${text}`, "Open message", () => {
    locationMessage(); // Reject a location that expired while the confirmation was open.
    const separator = /iPhone|iPad|iPod/.test(navigator.userAgent) ? "&" : "?";
    window.location.href = `sms:${phone}${separator}body=${encodeURIComponent(text)}`;
    notice("Message screen requested. Review and send it on your phone.");
  });
});
$("share-scene").onclick = () => guarded(() => {
  const current = requireScene();
  const bytes = Uint8Array.from(atob(current.data_url.split(",")[1]), c => c.charCodeAt(0));
  const file = new File([bytes], current.data_url.startsWith("data:image/png") ? "scene.png" : "scene.jpg", { type: current.data_url.split(";")[0].slice(5) });
  if (!navigator.share || !navigator.canShare?.({ files: [file] })) throw new Error("Photo sharing is unavailable here. Use a supported phone browser over trusted HTTPS.");
  confirmAction(`Share this one scene image? ${sceneLabel(current)} You will choose who receives it in your phone’s share screen. Location is not attached.`, "Choose recipient", async () => {
    if (scene?.id !== current.id) throw new Error("The selected scene changed. Please review it again.");
    try { await navigator.share({ files: [file], text: `I need help understanding this scene. ${sceneLabel(current)}` });
      notice("Share screen closed. Delivery is handled by the app you chose.");
    } catch (error) { if (error.name === "AbortError") notice("Sharing canceled."); else throw error; }
  });
});
function stopLocator() {
  clearTimeout(locatorTimer);
  if (locatorContext) { locatorContext.close(); locatorContext = null; }
}
$("locator").onclick = () => guarded(() => {
  stopLocator();
  const Audio = window.AudioContext || window.webkitAudioContext;
  if (!Audio) throw new Error("Locator audio is unavailable in this browser.");
  locatorContext = new Audio(); locatorContext.resume();
  const oscillator = locatorContext.createOscillator(), gain = locatorContext.createGain();
  oscillator.frequency.value = 880; oscillator.connect(gain); gain.connect(locatorContext.destination);
  const now = locatorContext.currentTime;
  for (let i = 0; i < 15; i++) {
    gain.gain.setValueAtTime(0, now + i); gain.gain.linearRampToValueAtTime(.35, now + i + .02);
    gain.gain.setValueAtTime(.35, now + i + .3); gain.gain.linearRampToValueAtTime(0, now + i + .35);
  }
  oscillator.start(); locatorTimer = setTimeout(stopLocator, 15000);
  notice("Locator sound started on this phone. Adjust your phone volume if needed.");
});
$("stop-locator").onclick = stopLocator;
$("help-close").onclick = () => { stopLocator(); $("help-controls").hidden = true; $("help-open").focus(); };
$("speak-landmark").onclick = () => speak($("landmark").textContent);
$("clear-local").onclick = () => {
  try { localStorage.removeItem("companion-contact"); localStorage.removeItem("companion-landmark"); } catch { /* Unavailable. */ }
  $("contact").value = ""; lastLandmark = null; locationFix = null; updateLandmark();
  $("location-status").textContent = "Phone location has not been requested."; notice("Saved number, landmark, and phone location cleared.");
};
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
if (Recognition) {
  $("dictate").hidden = false; $("dictation-note").hidden = false;
  $("dictate").onclick = () => guarded(() => {
    recognition?.abort(); recognition = new Recognition(); recognition.lang = navigator.language;
    recognition.onresult = event => { $("question").value = event.results[0][0].transcript.slice(0, 1000); notice("Question transcribed. Review it, then choose Ask."); };
    recognition.onerror = () => notice("Dictation unavailable. Type a question or use an example.");
    recognition.start(); notice("Listening for your question.");
  });
}
if (navigator.getBattery) {
  navigator.getBattery().then(battery => {
    const update = () => { $("battery-status").textContent = `This browser device’s battery: ${Math.round(battery.level * 100)}%${battery.charging ? ", charging" : ""}.`; };
    update(); battery.addEventListener("levelchange", update); battery.addEventListener("chargingchange", update);
  }).catch(() => { $("battery-status").textContent = "Phone battery: unavailable in this browser."; });
} else $("battery-status").textContent = "Phone battery: unavailable in this browser. Check the phone’s battery indicator.";
window.addEventListener("offline", () => notice("Browser reports no network. Calling and locator controls may still be available."));
window.addEventListener("pagehide", () => { stopLocator(); recognition?.abort(); window.speechSynthesis?.cancel(); });
updateLandmark();
