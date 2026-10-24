"""The two network methods being compared.

Method A: plain TCP socket   (client <-> bridge directly)
Method B: MQTT publish/sub   (client <-> Mosquitto broker <-> bridge)
"""
import socket
import socketserver
import threading
import uuid

from .protocol import decode, encode


# ----------------------------------------------------------------- Method A
class SocketClientTransport:
    def __init__(self, host="127.0.0.1", port=9009):
        self.host, self.port = host, port
        self.handler = None
        self.sock = None
        self._lock = threading.Lock()

    def set_handler(self, fn):
        self.handler = fn

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=5)
        self.sock.settimeout(None)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._rf = self.sock.makefile("rb")
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        try:
            for line in self._rf:
                try:
                    msg = decode(line)
                except ValueError:
                    continue
                if self.handler:
                    self.handler(msg)
        except (OSError, ValueError):
            pass

    def send(self, msg):
        with self._lock:
            try:
                self.sock.sendall(encode(msg))
            except OSError:
                pass

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


class _LineHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        for line in self.rfile:
            try:
                msg = decode(line)
            except ValueError:
                continue
            reply = self.server.bridge.handle(msg)
            try:
                self.wfile.write(encode(reply))
                self.wfile.flush()
            except OSError:
                break


class _TCP(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class SocketServer:
    def __init__(self, bridge, host="127.0.0.1", port=9009):
        self.srv = _TCP((host, port), _LineHandler)
        self.srv.bridge = bridge

    @property
    def port(self):
        return self.srv.server_address[1]

    def start(self):
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


# ----------------------------------------------------------------- Method B
def _new_mqtt(client_id):
    import paho.mqtt.client as mqtt
    return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)


def _failed(reason_code):
    return getattr(reason_code, "is_failure", reason_code != 0)


class MqttClientTransport:
    CMD_TOPIC = "p11/cmd"

    def __init__(self, host="127.0.0.1", port=1883, qos=0, client_id=None):
        self.host, self.port, self.qos = host, port, qos
        self.cid = client_id or "teleop-" + uuid.uuid4().hex[:8]
        self.handler = None

    def set_handler(self, fn):
        self.handler = fn

    def connect(self):
        self._ready = threading.Event()
        c = self.c = _new_mqtt(self.cid)
        c.on_connect = self._on_connect
        c.on_subscribe = lambda *a: self._ready.set()
        c.on_message = self._on_message
        c.connect(self.host, self.port, keepalive=30)
        c.loop_start()
        if not self._ready.wait(5):
            raise ConnectionError(
                f"MQTT broker at {self.host}:{self.port} did not answer")

    def _on_connect(self, c, userdata, flags, reason_code, properties=None):
        if not _failed(reason_code):
            c.subscribe(f"p11/reply/{self.cid}", qos=self.qos)

    def _on_message(self, c, userdata, m):
        try:
            msg = decode(m.payload)
        except ValueError:
            return
        if self.handler:
            self.handler(msg)

    def send(self, msg):
        msg = dict(msg, client=self.cid)   # reply topic is per transport id
        self.c.publish(self.CMD_TOPIC, encode(msg).decode(), qos=self.qos)

    def close(self):
        try:
            self.c.loop_stop()
            self.c.disconnect()
        except Exception:
            pass


class MqttServer:
    def __init__(self, bridge, host="127.0.0.1", port=1883, qos=0):
        self.bridge, self.host, self.port, self.qos = bridge, host, port, qos

    def start(self):
        self._ready = threading.Event()
        c = self.c = _new_mqtt("p11-bridge-" + uuid.uuid4().hex[:6])
        c.on_connect = self._on_connect
        c.on_subscribe = lambda *a: self._ready.set()
        c.on_message = self._on_message
        c.connect(self.host, self.port, keepalive=30)
        c.loop_start()
        if not self._ready.wait(5):
            raise ConnectionError(
                f"MQTT broker at {self.host}:{self.port} did not answer")

    def _on_connect(self, c, userdata, flags, reason_code, properties=None):
        if not _failed(reason_code):
            c.subscribe(MqttClientTransport.CMD_TOPIC, qos=self.qos)

    def _on_message(self, c, userdata, m):
        try:
            msg = decode(m.payload)
        except ValueError:
            return
        reply = self.bridge.handle(msg)
        cid = str(msg.get("client", "anon"))
        c.publish(f"p11/reply/{cid}", encode(reply).decode(), qos=self.qos)

    def stop(self):
        try:
            self.c.loop_stop()
            self.c.disconnect()
        except Exception:
            pass
