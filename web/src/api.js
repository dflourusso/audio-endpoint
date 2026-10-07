async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.message || "A operação falhou.");
  }
  return data;
}

export const api = {
  system: () => request("/api/system"),
  hostname: (hostname) => request("/api/system/hostname", { method: "POST", body: JSON.stringify({ hostname }) }),
  bluetoothStatus: () => request("/api/bluetooth/status"),
  scan: () => request("/api/bluetooth/scan", { method: "POST", body: "{}" }),
  scanResult: (jobId) => request(`/api/bluetooth/scan/${jobId}`),
  pair: (mac, name) => request("/api/bluetooth/pair", { method: "POST", body: JSON.stringify({ mac, name }) }),
  pairResult: (jobId, mac, name) =>
    request(`/api/bluetooth/pair/${jobId}?mac=${encodeURIComponent(mac)}&name=${encodeURIComponent(name || "")}`),
  connect: (mac) => request("/api/bluetooth/connect", { method: "POST", body: JSON.stringify({ mac }) }),
  disconnect: (mac) => request("/api/bluetooth/disconnect", { method: "POST", body: JSON.stringify({ mac }) }),
  forget: (mac) => request("/api/bluetooth/forget", { method: "POST", body: JSON.stringify({ mac }) }),
  outputs: () => request("/api/audio/outputs"),
  selectOutput: (id) => request("/api/audio/output", { method: "POST", body: JSON.stringify({ id }) }),
  sendspin: () => request("/api/sendspin/status"),
  musicAssistant: () => request("/api/music-assistant/status"),
  logs: (source) => request(`/api/logs?source=${encodeURIComponent(source)}&lines=150`),
  restart: (target) => request("/api/maintenance/restart", { method: "POST", body: JSON.stringify({ target }) }),
  reboot: () => request("/api/maintenance/reboot", { method: "POST", body: "{}" }),
  update: () => request("/api/maintenance/update", { method: "POST", body: "{}" }),
  updateStatus: () => request("/api/maintenance/update"),
  wifiStatus: () => request("/api/wifi/status"),
  wifiNetworks: () => request("/api/wifi/networks"),
  wifiRefresh: () => request("/api/wifi/refresh", { method: "POST", body: "{}" }),
  wifiConnect: (ssid, password) =>
    request("/api/wifi/connect", { method: "POST", body: JSON.stringify({ ssid, password }) }),
};

export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
