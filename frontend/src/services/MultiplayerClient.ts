/**
 * Dedicated WebSocket Client for AISR Multiplayer Backend.
 * Handles monotonic sequence numbers, heartbeat watchdog, server event routing,
 * and session state cleanly isolated from React UI components.
 */

import { PROTOCOL_VERSION } from '../types/protocol';
import type {
  ClientMessage,
  ConnectionStatus,
  ControlState,
  ErrorMessage,
  PlayerJoinedMessage,
  PlayerLeftMessage,
  PlayerReadyMessage,
  RobotAssignedMessage,
  RobotUnavailableMessage,
  RoomBroadcastMessage,
  RoomListMessage,
  RoomSnapshotMessage,
  SafetyEventMessage,
  ServerMessage,
  WelcomeMessage,
} from '../types/protocol';

export type MessageHandler<T extends ServerMessage> = (msg: T) => void;
export type ErrorHandler = (error: ErrorMessage) => void;
export type StatusHandler = (status: ConnectionStatus) => void;

export class MultiplayerClient {
  private ws: WebSocket | null = null;
  private clientSeq = 0;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private wsUrl?: string;

  public status: ConnectionStatus = 'DISCONNECTED';
  public sessionId: string | null = null;
  public welcomeData: WelcomeMessage | null = null;
  public currentRoom: RoomSnapshotMessage | null = null;
  public assignedRobotId: string | null = null;

  // Listeners
  public onStatusChange?: StatusHandler;
  public onWelcome?: MessageHandler<WelcomeMessage>;
  public onRoomSnapshot?: MessageHandler<RoomSnapshotMessage>;
  public onRoomList?: MessageHandler<RoomListMessage>;
  public onPlayerJoined?: MessageHandler<PlayerJoinedMessage>;
  public onPlayerLeft?: MessageHandler<PlayerLeftMessage>;
  public onPlayerReady?: MessageHandler<PlayerReadyMessage>;
  public onRobotAssigned?: MessageHandler<RobotAssignedMessage>;
  public onRobotUnavailable?: MessageHandler<RobotUnavailableMessage>;
  public onRoomBroadcast?: MessageHandler<RoomBroadcastMessage>;
  public onSafetyEvent?: MessageHandler<SafetyEventMessage>;
  public onError?: ErrorHandler;

  private lastInputSentTime = 0;

  constructor(wsUrl?: string) {
    this.wsUrl = wsUrl;
  }


  private setStatus(newStatus: ConnectionStatus) {
    this.status = newStatus;
    if (this.onStatusChange) {
      this.onStatusChange(newStatus);
    }
  }

  private nextSeq(): number {
    this.clientSeq += 1;
    return this.clientSeq;
  }

  private nowTs(): number {
    return Number((Date.now() / 1000).toFixed(3));
  }

  public connect(urlOverride?: string): Promise<void> {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return Promise.resolve();
    }

    const targetUrl =
      urlOverride ||
      this.wsUrl ||
      import.meta.env.VITE_WS_URL ||
      `ws://${window.location.hostname}:8000/ws/multiplayer`;

    this.setStatus('CONNECTING');

    return new Promise((resolve, reject) => {
      try {
        this.ws = new WebSocket(targetUrl);
      } catch (err) {
        this.setStatus('DISCONNECTED');
        reject(err);
        return;
      }

      this.ws.onopen = () => {
        // Socket opened; waiting for server WelcomeMessage
      };

      this.ws.onmessage = (event: MessageEvent) => {
        try {
          const data = JSON.parse(event.data) as ServerMessage;
          this.handleServerMessage(data);
          if (data.type === 'welcome') {
            resolve();
          }
        } catch (e) {
          console.error('Failed to parse incoming WebSocket message:', e);
        }
      };

      this.ws.onerror = (err) => {
        console.warn('WebSocket error encountered:', err);
      };

      this.ws.onclose = () => {
        this.stopHeartbeat();
        this.sessionId = null;
        this.welcomeData = null;
        this.currentRoom = null;
        this.assignedRobotId = null;
        this.setStatus('DISCONNECTED');
      };
    });
  }

  public disconnect(): void {
    this.stopHeartbeat();
    if (this.ws) {
      this.ws.close(1000, 'User disconnected');
      this.ws = null;
    }
    this.sessionId = null;
    this.welcomeData = null;
    this.currentRoom = null;
    this.assignedRobotId = null;
    this.setStatus('DISCONNECTED');
  }

  private startHeartbeat(intervalSec: number) {
    this.stopHeartbeat();
    const intervalMs = Math.max(1000, Math.floor(intervalSec * 1000));
    this.heartbeatTimer = setInterval(() => {
      if (this.status === 'CONNECTED' && this.sessionId) {
        this.send({
          v: PROTOCOL_VERSION,
          type: 'client_heartbeat',
          seq: this.nextSeq(),
          ts: this.nowTs(),
          session_id: this.sessionId,
        });
      }
    }, intervalMs);
  }

  private stopHeartbeat() {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  public send(msg: ClientMessage): boolean {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.warn('Cannot send message: WebSocket is not open.');
      return false;
    }
    try {
      this.ws.send(JSON.stringify(msg));
      return true;
    } catch (e) {
      console.error('WebSocket send failed:', e);
      return false;
    }
  }

  private handleServerMessage(msg: ServerMessage) {
    switch (msg.type) {
      case 'welcome':
        this.welcomeData = msg;
        this.sessionId = msg.session_id;
        this.setStatus('CONNECTED');
        this.startHeartbeat(msg.heartbeat_interval_sec || 2.0);
        this.onWelcome?.(msg);
        break;

      case 'room_snapshot':
        this.currentRoom = msg;
        // Verify current player's robot assignment
        if (this.sessionId) {
          const myMember = msg.members.find((m) => m.session_id === this.sessionId);
          if (myMember) {
            this.assignedRobotId = myMember.robot_id ?? null;
          }
        }
        this.onRoomSnapshot?.(msg);
        break;

      case 'robot_assigned':
        if (msg.session_id === this.sessionId) {
          this.assignedRobotId = msg.robot_id;
        }
        this.onRobotAssigned?.(msg);
        break;

      case 'robot_unavailable':
        if (msg.session_id === this.sessionId) {
          this.assignedRobotId = null;
        }
        this.onRobotUnavailable?.(msg);
        break;

      case 'player_joined':
        this.onPlayerJoined?.(msg);
        break;

      case 'player_left':
        if (msg.session_id === this.sessionId) {
          this.currentRoom = null;
          this.assignedRobotId = null;
        }
        this.onPlayerLeft?.(msg);
        break;

      case 'player_ready':
        this.onPlayerReady?.(msg);
        break;

      case 'room_list':
        this.onRoomList?.(msg);
        break;

      case 'room_broadcast':
        this.onRoomBroadcast?.(msg);
        break;

      case 'safety_event':
        this.onSafetyEvent?.(msg);
        break;

      case 'error':
        this.onError?.(msg);
        break;

      case 'command_ack':
        // Acknowledged
        break;

      default:
        console.warn('Unhandled server message:', msg);
        break;
    }
  }

  // -------------------------------------------------------------------------
  // High-Level Actions
  // -------------------------------------------------------------------------

  public createRoom(
    name?: string,
    capacity: number = 4,
    role: 'driver' | 'spectator' = 'driver',
    displayName?: string
  ): boolean {
    if (!this.sessionId) return false;
    return this.send({
      v: PROTOCOL_VERSION,
      type: 'create_room',
      seq: this.nextSeq(),
      ts: this.nowTs(),
      session_id: this.sessionId,
      name: name || null,
      capacity,
      role,
      display_name: displayName || null,
    });
  }

  public joinRoom(
    roomIdOrCode: string,
    displayName: string,
    role: 'driver' | 'spectator' = 'driver'
  ): boolean {
    if (!this.sessionId) return false;
    return this.send({
      v: PROTOCOL_VERSION,
      type: 'join_room',
      seq: this.nextSeq(),
      ts: this.nowTs(),
      session_id: this.sessionId,
      room_id: roomIdOrCode.trim().toUpperCase(),
      display_name: displayName.trim(),
      role,
    });
  }

  public leaveRoom(): boolean {
    if (!this.sessionId || !this.currentRoom) return false;
    const ok = this.send({
      v: PROTOCOL_VERSION,
      type: 'leave_room',
      seq: this.nextSeq(),
      ts: this.nowTs(),
      session_id: this.sessionId,
      room_id: this.currentRoom.room_id,
    });
    if (ok) {
      this.currentRoom = null;
      this.assignedRobotId = null;
    }
    return ok;
  }

  public setReady(isReady: boolean): boolean {
    if (!this.sessionId || !this.currentRoom) return false;
    return this.send({
      v: PROTOCOL_VERSION,
      type: 'set_ready',
      seq: this.nextSeq(),
      ts: this.nowTs(),
      session_id: this.sessionId,
      room_id: this.currentRoom.room_id,
      is_ready: isReady,
    });
  }

  public listRooms(): boolean {
    if (!this.sessionId) return false;
    return this.send({
      v: PROTOCOL_VERSION,
      type: 'list_rooms',
      seq: this.nextSeq(),
      ts: this.nowTs(),
      session_id: this.sessionId,
    });
  }

  public sendInput(
    throttle: number,
    steering: number,
    controlState: ControlState = 'active'
  ): boolean {
    if (!this.sessionId || !this.currentRoom || !this.assignedRobotId) return false;

    const now = performance.now();
    const isNeutral =
      controlState === 'neutral' ||
      controlState === 'released' ||
      (Math.abs(throttle) < 1e-4 && Math.abs(steering) < 1e-4);

    // Apply client-side rate limiting (max 30 Hz -> 33ms interval), unless releasing/neutral
    if (!isNeutral && now - this.lastInputSentTime < 33) {
      return false;
    }
    this.lastInputSentTime = now;

    const clampedThrottle = Math.max(-1.0, Math.min(1.0, throttle));
    const clampedSteering = Math.max(-1.0, Math.min(1.0, steering));

    return this.send({
      v: PROTOCOL_VERSION,
      type: 'player_input',
      seq: this.nextSeq(),
      ts: this.nowTs(),
      session_id: this.sessionId,
      throttle: Number(clampedThrottle.toFixed(3)),
      steering: Number(clampedSteering.toFixed(3)),
      control_state: controlState,
    });
  }

  public sendStop(reason: string = 'Normal stop'): boolean {
    if (!this.sessionId || !this.currentRoom) return false;
    return this.send({
      v: PROTOCOL_VERSION,
      type: 'individual_stop',
      seq: this.nextSeq(),
      ts: this.nowTs(),
      session_id: this.sessionId,
      reason,
    });
  }

  public sendEstop(reason: string = 'Player emergency stop'): boolean {
    if (!this.sessionId || !this.currentRoom) return false;
    return this.send({
      v: PROTOCOL_VERSION,
      type: 'individual_estop',
      seq: this.nextSeq(),
      ts: this.nowTs(),
      session_id: this.sessionId,
      reason,
    });
  }

  public sendRoomEstop(reason: string = 'Room emergency stop'): boolean {
    if (!this.sessionId || !this.currentRoom) return false;
    return this.send({
      v: PROTOCOL_VERSION,
      type: 'room_estop',
      seq: this.nextSeq(),
      ts: this.nowTs(),
      session_id: this.sessionId,
      room_id: this.currentRoom.room_id,
      reason,
    });
  }

  public sendRoomReset(): boolean {
    if (!this.sessionId || !this.currentRoom) return false;
    return this.send({
      v: PROTOCOL_VERSION,
      type: 'room_reset',
      seq: this.nextSeq(),
      ts: this.nowTs(),
      session_id: this.sessionId,
      room_id: this.currentRoom.room_id,
    });
  }
}
