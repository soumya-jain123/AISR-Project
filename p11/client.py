"""Operator-side client: sends commands, retries on timeout, sends a heartbeat
so the bridge watchdog knows the link is alive, and keeps latency statistics."""
import csv
import itertools
import statistics
import threading
import time
import uuid

from .netsim import ImpairedTransport
from .transports import MqttClientTransport, SocketClientTransport


class TeleopClient:
    def __init__(self, transport, timeout=0.5, retries=2, heartbeat_hz=10,
                 log_path=None):
        self.transport = transport
        self.cid = uuid.uuid4().hex[:8]
        self.timeout, self.retries, self.hb_period = timeout, retries, 1.0 / heartbeat_hz
        self._ids = itertools.count(1)
        self._pending = {}
        self._lock = threading.Lock()
        self.latest = None            # newest telemetry dict received
        self.latest_t = 0.0
        self.rtt_ema = None           # ms
        self.ping_rtts = []           # ms
        self.records = []             # one dict per user command
        self._closed = threading.Event()
        self._log_f = self._log = None
        if log_path:
            self._log_f = open(log_path, "w", newline="")
            self._log = csv.writer(self._log_f)
            self._log.writerow(["id", "cmd", "args", "t_first_sent", "t_reply",
                                "rtt_ms", "attempts", "delivered", "ok", "msg"])
        transport.set_handler(self._on_reply)

    def connect(self):
        self.transport.connect()
        threading.Thread(target=self._heartbeat, daemon=True).start()

    def close(self):
        self._closed.set()
        self.transport.close()
        if self._log_f:
            self._log_f.close()

    # ---------------------------------------------------------------- inbound
    def _on_reply(self, msg):
        now = time.time()
        if msg.get("telemetry"):
            self.latest, self.latest_t = msg["telemetry"], now
        with self._lock:
            p = self._pending.get(msg.get("id"))
            if p is None:
                return
            p["reply"], p["t_reply"] = msg, now
            if p["kind"] == "ping":
                self._pending.pop(msg["id"], None)
        if p["kind"] == "ping":
            rtt = (now - p["t_attempt"]) * 1000
            self.ping_rtts.append(rtt)
            self._update_ema(rtt)
        else:
            p["event"].set()

    def _update_ema(self, rtt):
        self.rtt_ema = rtt if self.rtt_ema is None else 0.8 * self.rtt_ema + 0.2 * rtt

    # --------------------------------------------------------------- outbound
    def _send(self, mid, cmd, args):
        self.transport.send({"id": mid, "client": self.cid, "cmd": cmd,
                             "args": list(args), "t_sent": time.time(),
                             "rtt_ms": self.rtt_ema})

    def _heartbeat(self):
        while not self._closed.wait(self.hb_period):
            mid = next(self._ids)
            now = time.time()
            with self._lock:
                self._pending[mid] = {"kind": "ping", "t_attempt": now}
                for k in [k for k, p in self._pending.items()      # forget lost pings
                          if p["kind"] == "ping" and now - p["t_attempt"] > 5]:
                    del self._pending[k]
            self._send(mid, "ping", [])

    def send_command(self, cmd, args=()):
        """Blocking send with retries. Returns the reply dict or None."""
        mid = next(self._ids)
        p = {"kind": "cmd", "event": threading.Event(), "reply": None}
        with self._lock:
            self._pending[mid] = p
        t_first, attempts = time.time(), 0
        for _ in range(self.retries + 1):
            attempts += 1
            p["t_attempt"] = time.time()
            self._send(mid, cmd, args)
            wait = max(self.timeout, 3 * (self.rtt_ema or 0) / 1000.0)
            if p["event"].wait(wait):
                break
        with self._lock:
            self._pending.pop(mid, None)
        reply = p["reply"]
        rec = {"id": mid, "cmd": cmd, "args": list(args), "t_first": t_first,
               "attempts": attempts, "delivered": reply is not None,
               "ok": bool(reply and reply.get("ok")),
               "rtt_ms": (p["t_reply"] - t_first) * 1000 if reply else None,
               "msg": reply.get("msg") if reply else "timeout"}
        if reply and attempts == 1:
            self._update_ema((p["t_reply"] - p["t_attempt"]) * 1000)
        self.records.append(rec)
        if self._log:
            self._log.writerow([mid, cmd, list(args), round(t_first, 4),
                                round(p["t_reply"], 4) if reply else "",
                                "" if rec["rtt_ms"] is None else round(rec["rtt_ms"], 1),
                                attempts, rec["delivered"], rec["ok"], rec["msg"]])
            self._log_f.flush()
        return reply

    # ------------------------------------------------------------------ stats
    def summary(self):
        cmds = [r for r in self.records if r["cmd"] != "ping"]
        rtts = list(self.ping_rtts) + [r["rtt_ms"] for r in cmds
                                       if r["rtt_ms"] is not None and r["attempts"] == 1]
        out = {"n_cmds": len(cmds),
               "cmd_success": (sum(r["delivered"] for r in cmds) / len(cmds)) if cmds else 1.0,
               "mean_attempts": statistics.mean(r["attempts"] for r in cmds) if cmds else 1.0,
               "n_rtt": len(rtts)}
        if rtts:
            s = sorted(rtts)
            out.update(rtt_mean=statistics.mean(s), rtt_p95=s[int(0.95 * (len(s) - 1))],
                       rtt_max=s[-1])
        else:
            out.update(rtt_mean=float("nan"), rtt_p95=float("nan"), rtt_max=float("nan"))
        return out


def build_client(transport="socket", host="127.0.0.1", port=9009,
                 mqtt_host="127.0.0.1", mqtt_port=1883, qos=0,
                 delay_ms=0.0, loss=0.0, seed=None, timeout=0.5, retries=2,
                 log_path=None):
    if transport == "socket":
        inner = SocketClientTransport(host, port)
    elif transport == "mqtt":
        inner = MqttClientTransport(mqtt_host, mqtt_port, qos=qos)
    else:
        raise ValueError("transport must be 'socket' or 'mqtt'")
    tr = ImpairedTransport(inner, delay_ms, loss, seed) if (delay_ms or loss) else inner
    return TeleopClient(tr, timeout=timeout, retries=retries, log_path=log_path)
