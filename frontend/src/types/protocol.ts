/**
 * Multiplayer Protocol Type Definitions for Browser Client.
 * Strict 1:1 mapping with backend schemas in multiplayer/protocol.py.
 */

export const PROTOCOL_VERSION = 1;

export type ConnectionStatus = 'DISCONNECTED' | 'CONNECTING' | 'CONNECTED' | 'RECONNECTING';

export const ErrorCode = {
  ERR_PAYLOAD_TOO_LARGE: 'ERR_PAYLOAD_TOO_LARGE',
  ERR_MALFORMED_JSON: 'ERR_MALFORMED_JSON',
  ERR_MISSING_HEADER: 'ERR_MISSING_HEADER',
  ERR_UNSUPPORTED_VERSION: 'ERR_UNSUPPORTED_VERSION',
  ERR_UNKNOWN_TYPE: 'ERR_UNKNOWN_TYPE',
  ERR_VALIDATION_FAILED: 'ERR_VALIDATION_FAILED',
  ERR_INVALID_NUMERIC: 'ERR_INVALID_NUMERIC',
  ERR_OUT_OF_BOUNDS: 'ERR_OUT_OF_BOUNDS',
  ERR_UNAUTHORIZED: 'ERR_UNAUTHORIZED',
  ERR_ESTOP_ACTIVE: 'ERR_ESTOP_ACTIVE',
  ERR_COMM_LOST: 'ERR_COMM_LOST',
  ERR_ROOM_NOT_FOUND: 'ERR_ROOM_NOT_FOUND',
  ERR_ROOM_FULL: 'ERR_ROOM_FULL',
  ERR_ALREADY_IN_ROOM: 'ERR_ALREADY_IN_ROOM',
  ERR_NOT_IN_ROOM: 'ERR_NOT_IN_ROOM',
  ERR_NO_ROBOTS_AVAILABLE: 'ERR_NO_ROBOTS_AVAILABLE',
  ERR_ROBOT_UNAVAILABLE: 'ERR_ROBOT_UNAVAILABLE',
} as const;

export type ErrorCode = (typeof ErrorCode)[keyof typeof ErrorCode];


// ---------------------------------------------------------------------------
// Base Envelope
// ---------------------------------------------------------------------------
export interface BaseMultiplayerEnvelope {
  v: number;
  type: string;
  seq: number;
  ts: number;
}

// ---------------------------------------------------------------------------
// Client -> Server Messages
// ---------------------------------------------------------------------------
export interface ClientHeartbeatMessage extends BaseMultiplayerEnvelope {
  type: 'client_heartbeat';
  session_id: string;
}

export interface CreateRoomMessage extends BaseMultiplayerEnvelope {
  type: 'create_room';
  session_id: string;
  name?: string | null;
  capacity?: number | null;
  role?: 'driver' | 'spectator';
  display_name?: string | null;
}

export interface JoinRoomMessage extends BaseMultiplayerEnvelope {
  type: 'join_room';
  session_id: string;
  room_id: string;
  display_name: string;
  role?: 'driver' | 'spectator';
}

export interface LeaveRoomMessage extends BaseMultiplayerEnvelope {
  type: 'leave_room';
  session_id: string;
  room_id: string;
}

export interface SetReadyMessage extends BaseMultiplayerEnvelope {
  type: 'set_ready';
  session_id: string;
  room_id: string;
  is_ready: boolean;
}

export interface ListRoomsMessage extends BaseMultiplayerEnvelope {
  type: 'list_rooms';
  session_id: string;
}

export type ClientMessage =
  | ClientHeartbeatMessage
  | CreateRoomMessage
  | JoinRoomMessage
  | LeaveRoomMessage
  | SetReadyMessage
  | ListRoomsMessage;

// ---------------------------------------------------------------------------
// Server -> Client Payloads and Messages
// ---------------------------------------------------------------------------
export interface WelcomeMessage extends BaseMultiplayerEnvelope {
  type: 'welcome';
  session_id: string;
  heartbeat_interval_sec: number;
  timeout_sec: number;
  server_time: number;
  message: string;
}

export interface CommandAckMessage extends BaseMultiplayerEnvelope {
  type: 'command_ack';
  ref_seq: number;
  ok: boolean;
  code?: string | null;
  message: string;
  state?: string | null;
}

export interface ErrorMessage extends BaseMultiplayerEnvelope {
  type: 'error';
  code: string;
  message: string;
  ref_seq?: number | null;
}

export interface RoomSummaryPayload {
  room_id: string;
  room_code: string;
  name: string;
  owner_id: string;
  player_count: number;
  capacity: number;
  status: string;
}

export interface RoomListMessage extends BaseMultiplayerEnvelope {
  type: 'room_list';
  rooms: RoomSummaryPayload[];
}

export interface RoomMemberPayload {
  session_id: string;
  display_name: string;
  role: string;
  robot_id?: string | null;
  is_ready: boolean;
  is_owner: boolean;
  joined_at: number;
}

export interface RoomSnapshotMessage extends BaseMultiplayerEnvelope {
  type: 'room_snapshot';
  room_id: string;
  room_code: string;
  name: string;
  owner_id: string;
  capacity: number;
  status: string;
  members: RoomMemberPayload[];
}

export interface PlayerJoinedMessage extends BaseMultiplayerEnvelope {
  type: 'player_joined';
  room_id: string;
  session_id: string;
  display_name: string;
  role: string;
  robot_id?: string | null;
}

export interface PlayerLeftMessage extends BaseMultiplayerEnvelope {
  type: 'player_left';
  room_id: string;
  session_id: string;
  reason: string;
  new_owner_id?: string | null;
}

export interface PlayerReadyMessage extends BaseMultiplayerEnvelope {
  type: 'player_ready';
  room_id: string;
  session_id: string;
  is_ready: boolean;
}

export interface RobotAssignedMessage extends BaseMultiplayerEnvelope {
  type: 'robot_assigned';
  room_id: string;
  session_id: string;
  robot_id: string;
}

export interface RobotUnavailableMessage extends BaseMultiplayerEnvelope {
  type: 'robot_unavailable';
  room_id: string;
  session_id: string;
  robot_id: string;
  reason: string;
}

export type ServerMessage =
  | WelcomeMessage
  | CommandAckMessage
  | ErrorMessage
  | RoomListMessage
  | RoomSnapshotMessage
  | PlayerJoinedMessage
  | PlayerLeftMessage
  | PlayerReadyMessage
  | RobotAssignedMessage
  | RobotUnavailableMessage;
