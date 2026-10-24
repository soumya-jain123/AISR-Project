"""Start the robot bridge (run this first, on the machine with the simulator).

  python run_bridge.py --robot fake                 # no simulator, for testing
  python run_bridge.py --robot coppelia             # CoppeliaSim must be open
  python run_bridge.py --robot coppelia --transport both
"""
import argparse
import time

from p11.bridge import Bridge
from p11.robots import CoppeliaRobot, FakeRobot
from p11.transports import MqttServer, SocketServer


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--robot", choices=["fake", "coppelia"], default="coppelia")
    ap.add_argument("--transport", choices=["socket", "mqtt", "both"], default="both")
    ap.add_argument("--host", default="127.0.0.1",
                    help="listen address for the socket server (0.0.0.0 = all)")
    ap.add_argument("--port", type=int, default=9009)
    ap.add_argument("--mqtt-host", default="127.0.0.1")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--qos", type=int, default=0, choices=[0, 1])
    ap.add_argument("--coppelia-host", default="localhost")
    ap.add_argument("--no-safety", action="store_true", help="start with safety layer OFF")
    ap.add_argument("--no-adaptive", action="store_true", help="disable delay-adaptive speed")
    ap.add_argument("--log", default="bridge_log.csv", help="command log (CSV)")
    a = ap.parse_args()

    factory = (lambda: FakeRobot()) if a.robot == "fake" else \
              (lambda: CoppeliaRobot(a.coppelia_host))
    bridge = Bridge(factory, safety=not a.no_safety, adaptive=not a.no_adaptive,
                    log_path=a.log)
    print(f"Starting robot back-end: {a.robot} ...")
    bridge.start()
    servers = []
    try:
        if a.transport in ("socket", "both"):
            s = SocketServer(bridge, a.host, a.port)
            s.start()
            servers.append(s)
            print(f"Socket server listening on {a.host}:{a.port}")
        if a.transport in ("mqtt", "both"):
            m = MqttServer(bridge, a.mqtt_host, a.mqtt_port, qos=a.qos)
            m.start()
            servers.append(m)
            print(f"MQTT bridge connected to broker {a.mqtt_host}:{a.mqtt_port} "
                  f"(topics p11/cmd, p11/reply/<client>)")
        print(f"Safety layer: {'ON' if bridge.safety else 'OFF'} | Ctrl+C to quit")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down ...")
    except (ConnectionError, OSError) as e:
        print(f"ERROR: {e}\nIs Mosquitto running? (see README, step 3)")
    finally:
        for s in servers:
            s.stop()
        bridge.stop()


if __name__ == "__main__":
    main()
