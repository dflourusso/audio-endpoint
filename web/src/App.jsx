import { useEffect, useState } from "react";
import { api, sleep } from "./api";

const PAGES = [
  ["painel", "Painel"],
  ["wifi", "Wi-Fi"],
  ["bluetooth", "Bluetooth"],
  ["audio", "Áudio"],
  ["music", "Música"],
  ["sistema", "Sistema"],
  ["manutencao", "Manutenção"],
];

export default function App() {
  const [page, setPage] = useState("painel");
  const [setup, setSetup] = useState(false);

  useEffect(() => {
    api.wifiStatus().then((status) => {
      if (status.mode === "access-point") {
        setSetup(true);
        setPage("wifi");
      }
    }).catch(() => {});
  }, []);

  const pages = setup ? [["wifi", "Wi-Fi"]] : PAGES;
  return (
    <>
      <nav>
        {pages.map(([id, label]) => (
          <button key={id} className={page === id ? "active" : ""} onClick={() => setPage(id)}>
            {label}
          </button>
        ))}
      </nav>
      <main className="app">
        {setup && <div className="banner">Esta placa está na rede {`audio-setup`}. Abra o endereço 192.168.4.1 e escolha o Wi-Fi de casa.</div>}
        {page === "painel" && <Dashboard />}
        {page === "wifi" && <WifiPage onConnected={() => setSetup(false)} />}
        {page === "bluetooth" && <BluetoothPage />}
        {page === "audio" && <AudioPage />}
        {page === "music" && <MusicPage />}
        {page === "sistema" && <SystemPage />}
        {page === "manutencao" && <MaintenancePage />}
      </main>
    </>
  );
}

function useLoad(loader) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  async function reload() {
    setLoading(true);
    setError("");
    try {
      setData(await loader());
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    reload();
  }, []);
  return { data, error, loading, reload, setError };
}

function Banner({ error }) {
  if (!error) return null;
  return <div className="banner error">{error}</div>;
}

function Dashboard() {
  const { data, error, loading, reload } = useLoad(async () => {
    const [system, bluetooth, sendspin, audio, music] = await Promise.all([
      api.system(),
      api.bluetoothStatus(),
      api.sendspin(),
      api.outputs(),
      api.musicAssistant(),
    ]);
    return { system, bluetooth, sendspin, audio, music };
  });
  if (loading && !data) return <p>Carregando…</p>;
  const system = data?.system || {};
  const current = data?.audio?.outputs?.find((item) => item.active);
  return (
    <>
      <header>
        <h1>{system.hostname || "Audio Endpoint"}</h1>
        <p>{system.mdns || "Hostname ainda não anunciado"}</p>
      </header>
      <Banner error={error} />
      <section className="cards">
        <Card label="IP" value={system.ip || "—"} />
        <Card label="MAC" value={system.mac || "—"} />
        <Card label="Bluetooth" value={data?.bluetooth?.bridge_reachable ? "Bridge no ar" : "Bridge sem resposta"} />
        <Card label={speakerLabel(data?.bluetooth)} value={speakerValue(data?.bluetooth)} />
        <Card label="Sendspin" value={data?.sendspin?.reachable ? (data.sendspin.connected ? "Conectado" : "Anunciado") : "Indisponível"} />
        <Card label="Saída" value={current?.name || "Bluetooth"} />
        <Card label="Music Assistant" value={data?.music?.playing ? "Reproduzindo" : data?.music?.session_connected || data?.music?.ma_connected ? "Conectado" : "Aguardando"} />
        <Card label="Versão" value={system.project_version || "—"} />
      </section>
      <div className="stack" style={{ marginTop: 12 }}>
        <button className="secondary" onClick={reload}>Atualizar painel</button>
      </div>
    </>
  );
}

function speakerLabel(status) {
  const devices = status?.devices || [];
  if (devices.some((device) => device.playing)) return "Tocando";
  if (devices.filter((device) => device.connected).length > 1) return "Caixas conectadas";
  return "Caixa conectada";
}

function speakerValue(status) {
  const devices = status?.devices || [];
  const playing = devices.filter((device) => device.playing);
  if (playing.length) return playing.map((device) => device.name).join(", ");
  const connected = devices.filter((device) => device.connected);
  if (connected.length) return connected.map((device) => device.name).join(", ");
  return status?.connected_device?.name || "Nenhuma";
}

function Card({ label, value }) {
  return (
    <article className="card">
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function WifiPage({ onConnected }) {
  const { data, error, reload, setError } = useLoad(async () => {
    const [status, networks] = await Promise.all([api.wifiStatus(), api.wifiNetworks()]);
    return { status, networks: networks.networks || [] };
  });
  const [ssid, setSsid] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function refresh() {
    setBusy(true);
    setError("");
    try {
      await api.wifiRefresh();
      await reload();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function connect(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await api.wifiConnect(ssid, password);
      setMessage("Conectado. A placa vai sair da rede audio-setup e entrar na rede escolhida.");
      setPassword("");
      if (onConnected) onConnected();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const networks = data?.networks || [];
  return (
    <>
      <header>
        <h1>Wi-Fi</h1>
        <p>
          {data?.status?.mode === "access-point"
            ? "Modo de configuração. As redes abaixo foram vistas antes de abrir o ponto de acesso. Buscar de novo desliga o rádio por alguns segundos e o celular pode cair da rede."
            : "A placa usa as redes salvas aqui. Se nenhuma responder, ela abre audio-setup."}
        </p>
      </header>
      <Banner error={error} />
      {message && <div className="banner">{message}</div>}
      <form className="stack" onSubmit={connect}>
        <label>
          Rede
          <select value={ssid} onChange={(event) => setSsid(event.target.value)} required>
            <option value="">Escolha uma rede</option>
            {networks.map((network) => (
              <option key={network.ssid} value={network.ssid}>
                {network.ssid} · {network.security} · {network.signal}%
              </option>
            ))}
          </select>
        </label>
        <label>
          Senha
          <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoCapitalize="none" autoCorrect="off" />
        </label>
        <button type="submit" disabled={busy || !ssid}>{busy ? "Conectando…" : "Conectar"}</button>
        <button className="secondary" type="button" disabled={busy} onClick={refresh}>Buscar de novo</button>
      </form>
    </>
  );
}

function WebhookField({ device, busy, onSave }) {
  const [value, setValue] = useState(device.airplay_webhook || "");
  useEffect(() => {
    setValue(device.airplay_webhook || "");
  }, [device.airplay_webhook]);
  if (!device.in_fleet) return null;
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        onSave(device.mac, value);
      }}
      style={{ marginTop: 10 }}
    >
      <span>AirPlay: {device.airplay_name || "—"}</span>
      <label className="muted" htmlFor={`hook-${device.mac}`}>Webhook ao começar o AirPlay</label>
      <input
        id={`hook-${device.mac}`}
        value={value}
        placeholder="Opcional. Vazio não chama ninguém."
        onChange={(event) => setValue(event.target.value)}
        autoComplete="off"
        spellCheck="false"
      />
      <div className="actions" style={{ marginTop: 8 }}>
        <button className="secondary" type="submit" disabled={Boolean(busy)}>Salvar webhook</button>
      </div>
    </form>
  );
}

function BluetoothPage() {
  const { data, error, reload, setError } = useLoad(api.bluetoothStatus);
  const [found, setFound] = useState([]);
  const [busy, setBusy] = useState("");

  async function scan() {
    setBusy("scan");
    setError("");
    try {
      const { job_id: jobId } = await api.scan();
      for (let attempt = 0; attempt < 20; attempt += 1) {
        await sleep(1000);
        const result = await api.scanResult(jobId);
        if (result.status === "done") {
          if (result.error) throw new Error(result.error);
          setFound(result.devices || []);
          break;
        }
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy("");
    }
  }

  async function pair(device) {
    setBusy(device.mac);
    setError("");
    try {
      const { job_id: jobId } = await api.pair(device.mac, device.name, device.adapter);
      for (let attempt = 0; attempt < 40; attempt += 1) {
        await sleep(1000);
        const result = await api.pairResult(jobId, device.mac, device.name);
        if (result.status === "done") {
          if (!result.success) throw new Error(result.error || "O pareamento falhou.");
          break;
        }
      }
      await reload();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy("");
    }
  }

  async function act(label, job) {
    setBusy(label);
    setError("");
    try {
      await job();
      await reload();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy("");
    }
  }

  const devices = data?.devices || [];
  return (
    <>
      <header>
        <h1>Bluetooth</h1>
        <p>{speakerLabel(data)}: {speakerValue(data)}</p>
      </header>
      <Banner error={error} />
      <div className="stack">
        <button onClick={scan} disabled={busy === "scan"}>{busy === "scan" ? "Buscando…" : "Buscar dispositivos"}</button>
        {found.map((device) => (
          <article className="row" key={device.mac}>
            <div>
              <strong>{device.name}</strong>
              <span className="muted">{device.mac}</span>
            </div>
            <button onClick={() => pair(device)} disabled={Boolean(busy)}>Parear</button>
          </article>
        ))}
        <h2>Conhecidos</h2>
        {devices.length === 0 && <p className="muted">Nenhum dispositivo pareado.</p>}
        {devices.map((device) => (
          <article className="card" key={device.mac}>
            <strong>{device.name}</strong>
            <span>
              {device.mac}
              {" · "}
              {device.playing ? "reproduzindo" : device.connected ? "Bluetooth conectado" : "Bluetooth desconectado"}
              {" · "}
              {device.announced ? "anunciada ao Music Assistant" : device.in_fleet ? "player ainda não subiu" : "só pareada"}
            </span>
            {device.in_fleet && (
              <WebhookField
                device={device}
                busy={busy}
                onSave={(mac, webhook) => act(mac, () => api.airplayWebhook(mac, webhook))}
              />
            )}
            <div className="actions" style={{ marginTop: 10 }}>
              <button className="secondary" disabled={Boolean(busy)} onClick={() => act(device.mac, () => api.connect(device.mac, device.bridge_player))}>Conectar</button>
              <button className="secondary" disabled={Boolean(busy)} onClick={() => act(device.mac, () => api.disconnect(device.mac))}>Desconectar</button>
              <button className="danger" disabled={Boolean(busy)} onClick={() => {
                if (window.confirm(`Esquecer ${device.name}?`)) act(device.mac, () => api.forget(device.mac));
              }}>Esquecer</button>
            </div>
          </article>
        ))}
      </div>
    </>
  );
}

function AudioPage() {
  const { data, error, setError, reload } = useLoad(api.outputs);
  async function choose(id) {
    setError("");
    try {
      await api.selectOutput(id);
      await reload();
    } catch (err) {
      setError(err.message);
    }
  }
  return (
    <>
      <header>
        <h1>Áudio</h1>
        <p>A reprodução desta versão sai pelo Bluetooth.</p>
      </header>
      <Banner error={error} />
      <div className="stack">
        {(data?.outputs || []).map((output) => (
          <article className="card" key={output.id}>
            <strong>{output.name}</strong>
            <span>{output.description}</span>
            <span>{output.active ? "Saída atual" : output.selectable ? "Disponível" : "Detectada, seleção ainda não disponível"}</span>
            {output.selectable && !output.active && <button onClick={() => choose(output.id)}>Usar</button>}
          </article>
        ))}
      </div>
    </>
  );
}

function MusicPage() {
  const { data, error, loading, reload } = useLoad(async () => {
    const [sendspin, music, airplay] = await Promise.all([api.sendspin(), api.musicAssistant(), api.airplay()]);
    return { sendspin, music, airplay };
  });
  const names = data?.airplay?.names || [];
  const airplayName = names.length ? names.join(", ") : "—";
  return (
    <>
      <header>
        <h1>Música</h1>
        <p>O Music Assistant continua pelo Sendspin. No iPhone, cada caixa da frota aparece como um AirPlay com o nome do player.</p>
      </header>
      <Banner error={error} />
      {loading && !data ? <p>Carregando…</p> : (
        <section className="cards">
          <Card label="Sendspin" value={data?.sendspin?.reachable ? (data?.music?.playing ? "Reproduzindo" : data?.sendspin?.connected ? "Conectado" : "No ar") : "Sem resposta"} />
          <Card label="Player" value={data?.sendspin?.player_name || data?.music?.player_name || "—"} />
          <Card label="Conexão MA" value={data?.music?.playing ? "Reproduzindo" : data?.music?.session_connected || data?.music?.ma_connected ? "Conectado" : "Aguardando"} />
          <Card label="Modo" value={data?.music?.runtime_mode || "—"} />
          <Card label="AirPlay" value={data?.airplay?.advertising ? airplayName : "Fora do ar"} />
          <Card label="Sessão AirPlay" value={data?.airplay?.playing ? "Reproduzindo" : "Parada"} />
          <article className="card wide">
            <span>Players anunciados</span>
            {(data?.music?.players || []).map((player) => (
              <strong key={player.player_name || player.mac}>
                {player.player_name || player.mac}
                {" · "}
                {player.playing ? "reproduzindo" : player.session_connected ? "conectado" : "anunciado"}
                {player.connected && !player.has_sink ? " · sem saída de áudio na caixa" : ""}
              </strong>
            ))}
            {(data?.music?.players || []).length === 0 && <strong>Nenhum player ainda</strong>}
          </article>
        </section>
      )}
      <div className="stack" style={{ marginTop: 12 }}>
        <button className="secondary" onClick={reload}>Atualizar</button>
      </div>
    </>
  );
}

function SystemPage() {
  const { data, error, reload } = useLoad(api.system);
  const [hostname, setHostname] = useState("");
  const [message, setMessage] = useState("");
  const [formError, setFormError] = useState("");

  async function save(event) {
    event.preventDefault();
    setFormError("");
    setMessage("");
    try {
      const result = await api.hostname(hostname);
      setMessage(result.warning || `Acesse http://${result.mdns} depois que o anúncio propagar.`);
      setHostname("");
      await reload();
    } catch (err) {
      setFormError(err.message);
    }
  }

  const memory = data?.memory;
  return (
    <>
      <header>
        <h1>Sistema</h1>
        <p>{data?.os || "Coletando informações"}</p>
      </header>
      <Banner error={error || formError} />
      {message && <div className="banner">{message}</div>}
      <section className="cards">
        <Card label="Hostname" value={data?.hostname || "—"} />
        <Card label="mDNS" value={data?.mdns || "—"} />
        <Card label="IP" value={data?.ip || "—"} />
        <Card label="MAC" value={data?.mac || "—"} />
        <Card label="CPU" value={data?.cpu_percent != null ? `${data.cpu_percent}%` : "—"} />
        <Card label="RAM" value={memory ? `${memory.used_mb}/${memory.total_mb} MB` : "—"} />
        <Card label="Armazenamento" value={data?.disk ? `${data.disk.used_gb}/${data.disk.total_gb} GB` : "—"} />
        <Card label="Temperatura" value={data?.temperature_c != null ? `${data.temperature_c} °C` : "Indisponível"} />
        <Card label="Uptime" value={formatUptime(data?.uptime_seconds)} />
        <Card label="Projeto" value={data?.project_version || "—"} />
        <Card label="Bridge" value={typeof data?.bridge_version === "string" ? data.bridge_version : "—"} />
      </section>
      <form className="stack" onSubmit={save} style={{ marginTop: 16 }}>
        <label>
          Novo hostname
          <input value={hostname} onChange={(event) => setHostname(event.target.value)} placeholder="audio-sala" autoCapitalize="none" autoCorrect="off" />
        </label>
        <button type="submit">Salvar hostname</button>
        <button className="secondary" type="button" onClick={reload}>Atualizar</button>
      </form>
    </>
  );
}

function MaintenancePage() {
  const [source, setSource] = useState("bridge");
  const [logs, setLogs] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function run(job) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await job();
      setMessage(result.message || "Pedido enviado.");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function loadLogs() {
    setError("");
    try {
      const result = await api.logs(source);
      setLogs(result.text || "");
    } catch (err) {
      setError(err.message);
    }
  }

  async function pollUpdate() {
    await run(async () => {
      const started = await api.update();
      for (let attempt = 0; attempt < 60; attempt += 1) {
        await sleep(2000);
        try {
          const status = await api.updateStatus();
          if (status.state === "succeeded" || status.state === "failed") return status;
        } catch {
          // A API reinicia no meio da atualização.
        }
      }
      return started;
    });
  }

  return (
    <>
      <header>
        <h1>Manutenção</h1>
        <p>Reiniciar a interface não apaga pareamentos nem o hostname.</p>
      </header>
      <Banner error={error} />
      {message && <div className="banner">{message}</div>}
      <div className="stack">
        <button className="secondary" disabled={busy} onClick={() => run(() => api.restart("app"))}>Reiniciar aplicação</button>
        <button className="secondary" disabled={busy} onClick={() => run(() => api.restart("bluetooth"))}>Reiniciar Bluetooth</button>
        <button className="secondary" disabled={busy} onClick={() => run(() => api.restart("sendspin"))}>Reiniciar Sendspin</button>
        <button className="danger" disabled={busy} onClick={() => {
          if (window.confirm("Reiniciar o Orange Pi?")) run(() => api.reboot());
        }}>Reiniciar o aparelho</button>
        <button disabled={busy} onClick={() => {
          if (window.confirm("Atualizar o software? As configurações desta unidade são preservadas.")) pollUpdate();
        }}>Atualizar software</button>
        <label>
          Logs
          <select value={source} onChange={(event) => setSource(event.target.value)}>
            <option value="bridge">Sendspin</option>
            <option value="agent">Agente</option>
            <option value="bluetooth">Bluetooth</option>
            <option value="avahi">Avahi</option>
          </select>
        </label>
        <button className="secondary" onClick={loadLogs}>Mostrar logs</button>
        <pre className="logs">{logs || "Nenhum log carregado."}</pre>
      </div>
    </>
  );
}

function formatUptime(seconds) {
  if (seconds == null) return "—";
  const total = Math.floor(seconds);
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}min`;
  return `${minutes} min`;
}
