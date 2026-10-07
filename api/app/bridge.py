import httpx


class BridgeError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class BridgeClient:
    def __init__(self, base_url: str, token: str = "", transport: httpx.BaseTransport | None = None):
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=httpx.Timeout(40.0, connect=5.0),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def request(self, method: str, path: str, json: dict | None = None) -> dict | list:
        try:
            response = self._client.request(method, path, json=json)
        except httpx.HTTPError as exc:
            raise BridgeError("O Sendspin Bluetooth Bridge não respondeu.") from exc
        if response.status_code >= 400:
            detail = _error_detail(response)
            raise BridgeError(detail, status_code=502)
        if not response.content:
            return {}
        data = response.json()
        if isinstance(data, (dict, list)):
            return data
        raise BridgeError("Resposta inesperada do bridge.")

    def status(self) -> dict:
        data = self.request("GET", "/api/status")
        return data if isinstance(data, dict) else {}

    def health(self) -> dict:
        data = self.request("GET", "/api/health")
        return data if isinstance(data, dict) else {}

    def version(self) -> dict:
        data = self.request("GET", "/api/version")
        return data if isinstance(data, dict) else {}

    def runtime_info(self) -> dict:
        data = self.request("GET", "/api/runtime-info")
        return data if isinstance(data, dict) else {}

    def config(self) -> dict:
        data = self.request("GET", "/api/config")
        return data if isinstance(data, dict) else {}

    def save_config(self, config: dict) -> dict:
        data = self.request("POST", "/api/config", json=config)
        return data if isinstance(data, dict) else {}

    def scan(self) -> dict:
        data = self.request("POST", "/api/bt/scan")
        return data if isinstance(data, dict) else {}

    def scan_result(self, job_id: str) -> dict:
        data = self.request("GET", f"/api/bt/scan/result/{job_id}")
        return data if isinstance(data, dict) else {}

    def paired(self) -> list:
        data = self.request("GET", "/api/bt/paired")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            devices = data.get("devices") or data.get("paired") or []
            return devices if isinstance(devices, list) else []
        return []

    def pair_new(self, mac: str, adapter: str = "") -> dict:
        body = {"mac": mac}
        if adapter:
            body["adapter"] = adapter
        data = self.request("POST", "/api/bt/pair_new", json=body)
        return data if isinstance(data, dict) else {}

    def pair_result(self, job_id: str) -> dict:
        data = self.request("GET", f"/api/bt/pair_new/result/{job_id}")
        return data if isinstance(data, dict) else {}

    def reconnect(self, mac: str) -> dict:
        data = self.request("POST", "/api/bt/reconnect", json={"mac": mac})
        return data if isinstance(data, dict) else {}

    def disconnect(self, mac: str) -> dict:
        data = self.request("POST", "/api/bt/disconnect", json={"mac": mac})
        return data if isinstance(data, dict) else {}

    def remove(self, mac: str) -> dict:
        data = self.request("POST", "/api/bt/remove", json={"mac": mac})
        return data if isinstance(data, dict) else {}

    def restart(self) -> dict:
        data = self.request("POST", "/api/restart")
        return data if isinstance(data, dict) else {}

    def logs(self, lines: int) -> dict:
        data = self.request("GET", f"/api/logs?lines={lines}")
        return data if isinstance(data, dict) else {"lines": data}


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        for key in ("error", "message", "detail"):
            if payload.get(key):
                return str(payload[key])
    return "O bridge recusou a operação."
