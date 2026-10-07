import json
import socket


class AgentError(Exception):
    def __init__(self, message: str, code: str = "agent_error"):
        super().__init__(message)
        self.message = message
        self.code = code


class AgentClient:
    def __init__(self, socket_path: str):
        self.socket_path = socket_path

    def call(self, action: str, **fields) -> dict:
        payload = {"action": action, **fields}
        raw = json.dumps(payload).encode("utf-8")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(90)
                client.connect(self.socket_path)
                client.sendall(raw)
                client.shutdown(socket.SHUT_WR)
                chunks = []
                while True:
                    piece = client.recv(65536)
                    if not piece:
                        break
                    chunks.append(piece)
        except (OSError, TimeoutError) as exc:
            raise AgentError("O agente do host não está disponível.") from exc
        try:
            data = json.loads(b"".join(chunks).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise AgentError("Resposta inválida do agente.") from exc
        if not isinstance(data, dict):
            raise AgentError("Resposta inválida do agente.")
        if not data.get("ok", False):
            raise AgentError(str(data.get("message") or "O agente recusou a operação."), str(data.get("error") or "agent_error"))
        return data
