# WHMI-Link Protocol, version 1

WHMI-Link connects WHMI Panels and WHMI Studio to a WHMI Receiver. It is designed to be
implementable on a microcontroller (fixed little-endian binary layout, no dynamic
schema, AES-GCM and HMAC-SHA256, which mbedTLS provides) as well as in Python and C#.

* Transport: TCP, default port **47800**
* Byte order: **little-endian** (like CIP/EtherNet/IP)
* Strings: `str8` = `u8 length` + UTF-8 bytes; `str16` = `u16 length` + UTF-8 bytes

## 1. Framing

Every message is a frame with a 12-byte header:

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | 2 | magic | `0x57 0x48` ("WH") |
| 2 | 1 | version | `1` |
| 3 | 1 | type | message type, see §4 |
| 4 | 1 | flags | bit0 = `ENCRYPTED` |
| 5 | 1 | reserved | must be `0` |
| 6 | 4 | seq | `u32` sequence number |
| 10 | 2 | length | `u16` payload length on the wire |

The maximum payload length is **16 384** bytes, including the 16-byte GCM tag. A larger length is a protocol error and
the connection is closed.

### Sequence numbers

Each direction has its own counter. The handshake frames (§2) use seq 0, 1, … in each
direction. The first encrypted frame in each direction uses seq **1**, and every frame after it
increments seq by exactly 1. A receiver of frames closes the connection on any gap, repeat
or wrap. Because TCP delivers in order, any deviation means an attack or a bug.

## 2. Handshake (mutual authentication)

Each client has a `client_id` and a 32-byte pre-shared `key`. The receiver stores one entry per client:
id, key, role. Handshake frames are plaintext (`flags = 0`).

```
Panel/Studio                                   Receiver
  HELLO     {ver, kind, client_id, Nc[16]} ──►
            ◄── CHALLENGE {receiver_id, Ns[16]}
  AUTH      {MACc[32]}                   ──►   verify MACc (constant time)
            ◄── AUTH_OK {MACs[32]}             (or AUTH_FAIL + close)
  verify MACs
  ============ all further frames encrypted =============
            ◄── SESSION_INFO {...}
```

* `ctx = Nc || Ns || str8(client_id) || str8(receiver_id)`
* `MACc = HMAC-SHA256(key, "WHMI-AUTH-C" || ctx)`
* `MACs = HMAC-SHA256(key, "WHMI-AUTH-S" || ctx)`
* `K_c2s = HKDF-SHA256(ikm=key, salt=Nc||Ns, info="WHMI-Link v1 c2s", L=32)`
* `K_s2c = HKDF-SHA256(ikm=key, salt=Nc||Ns, info="WHMI-Link v1 s2c", L=32)`

The receiver closes the connection if the handshake doesn't complete within 5 s. After a failed AUTH it waits 1 s before
sending AUTH_FAIL, to slow brute-force attempts. Unknown client ids get the same AUTH_FAIL as
a wrong key, so the receiver doesn't reveal which ids exist.

`kind`: 1 = panel, 2 = studio, 3 = tool.

## 3. Encryption

Every frame after AUTH_OK is encrypted with **AES-256-GCM**:

* key: `K_c2s` (client → receiver) or `K_s2c` (receiver → client)
* nonce (12 bytes): `"C2S\0"` or `"S2C\0"` (4 bytes) || `u64 seq`
* AAD: the 12-byte frame header (with `length` = ciphertext + tag length)
* payload on the wire: `ciphertext || tag[16]`

A frame that fails authentication closes the connection.

## 4. Message types

| Type | Name | Dir | Payload |
|---|---|---|---|
| 0x01 | HELLO | C→S | `u8 proto_ver, u8 kind, str8 client_id, Nc[16]` |
| 0x02 | CHALLENGE | S→C | `str8 receiver_id, Ns[16]` |
| 0x03 | AUTH | C→S | `MACc[32]` |
| 0x04 | AUTH_OK | S→C | `MACs[32]` |
| 0x05 | AUTH_FAIL | S→C | *(empty)* |
| 0x10 | HEARTBEAT | both | *(empty)* |
| 0x11 | SESSION_INFO | S→C | `u8 role, u16 heartbeat_ms, u16 watchdog_ms, hash[32], u32 project_size` |
| 0x20 | READ_REQ | C→S | `u16 req_id, u16 n, n × u16 tag_id` |
| 0x21 | READ_RSP | S→C | `u16 req_id, u16 n, n × TagValue` |
| 0x22 | WRITE_REQ | C→S | `u16 req_id, u16 n, n × (u16 tag_id, Value)` |
| 0x23 | WRITE_RSP | S→C | `u16 req_id, u16 n, n × (u16 tag_id, u8 status)` |
| 0x24 | SUBSCRIBE | C→S | `u16 req_id, u16 interval_ms, u16 n, n × u16 tag_id` |
| 0x25 | SUB_RSP | S→C | `u16 req_id, u16 n, n × (u16 tag_id, u8 status)` |
| 0x26 | UNSUBSCRIBE | C→S | `u16 req_id, u16 n, n × u16 tag_id` |
| 0x30 | DATA_UPDATE | S→C | `u16 n, n × TagValue` |
| 0x31 | MOMENTARY | C→S | `u16 req_id, u16 tag_id, Value, u16 hold_ms`, answered by WRITE_RSP |
| 0x40 | PROJECT_INFO_REQ | C→S | `u16 req_id` |
| 0x41 | PROJECT_INFO | S→C | `u16 req_id, hash[32], u32 size, str8 name` |
| 0x42 | PROJECT_READ | C→S | `u16 req_id, u32 offset, u16 length` |
| 0x43 | PROJECT_DATA | S→C | `u16 req_id, u8 status, u32 offset, u16 len, bytes` |
| 0x44 | PROJECT_WRITE_BEGIN | C→S | `u16 req_id, u32 size, hash[32]` |
| 0x45 | PROJECT_WRITE_CHUNK | C→S | `u16 req_id, u32 offset, u16 len, bytes` |
| 0x46 | PROJECT_WRITE_COMMIT | C→S | `u16 req_id` |
| 0x47 | PROJECT_WRITE_RSP | S→C | `u16 req_id, u8 status, str16 message` |
| 0x48 | PROJECT_CHANGED | S→C | `hash[32]` |
| 0x7F | ERROR | S→C | `u16 code, str16 message` |

### 4.1 Values

`Value = u8 type_code, data`. Type codes are the **CIP elementary data type codes**:

| Code | Type | Data |
|---|---|---|
| 0xC1 | BOOL | u8 (0/1) |
| 0xC2 | SINT | i8 |
| 0xC3 | INT | i16 |
| 0xC4 | DINT | i32 |
| 0xC5 | LINT | i64 |
| 0xC6 | USINT | u8 |
| 0xC7 | UINT | u16 |
| 0xC8 | UDINT | u32 |
| 0xC9 | ULINT | u64 |
| 0xCA | REAL | f32 |
| 0xCB | LREAL | f64 |
| 0xD0 | STRING | str16 (Logix STRING: max 82 chars) |

`TagValue = u16 tag_id, u8 quality, i64 timestamp_ms (Unix epoch), Value`

When the quality is bad, the receiver still sends a Value: the last known value or the type's zero.

### 4.2 Quality codes

| Code | Name | Meaning |
|---|---|---|
| 0x00 | GOOD | |
| 0x40 | UNCERTAIN | e.g. a value forced/overridden, or an analog value out of sensor range |
| 0x80 | BAD | generic |
| 0x81 | BAD_COMM | the receiver lost communication with the PLC/device |
| 0x82 | BAD_CONFIG | tag misconfigured (e.g. PLC tag doesn't exist) |
| 0x83 | BAD_NOT_CONNECTED | *panel-local*: the panel lost its link to the receiver |
| 0x84 | BAD_DEVICE | I/O module fault (wire break, module missing) |
| 0x85 | BAD_WAITING | no value acquired yet |

A quality is bad when bit 7 is set.

### 4.3 Status codes (`u8`)

`0 OK, 1 BAD_TAG, 2 READ_ONLY, 3 TYPE_MISMATCH, 4 OUT_OF_RANGE, 5 NOT_PERMITTED,
6 COMM_FAILURE, 7 BUSY, 8 INVALID, 9 INTERNAL`

### 4.4 Roles

| Code | Role | Rights |
|---|---|---|
| 1 | viewer | read and subscribe |
| 2 | operator | viewer + write tags with `access = read_write` |
| 3 | engineer | operator + project download |

## 5. Session behaviour

* **Heartbeat:** each side sends HEARTBEAT if it sent nothing else for `heartbeat_ms` (default 250).
* **Watchdog:** each side closes the session if it received nothing for `watchdog_ms` (default 1000).
  Closing a session *triggers the receiver's link-loss actions* (§6).
* **Subscriptions:** the receiver sends a DATA_UPDATE with all current values right after SUB_RSP.
  After that, every `interval_ms` it sends only the values that changed (honouring each tag's
  deadband). A DATA_UPDATE is limited to the max payload size and is split into several frames if needed.
* **Project transfer:** the client reads in chunks of ≤ 8192 bytes. Downloads (engineer role only)
  are staged. Then COMMIT verifies the SHA-256 hash and validates the project before it is atomically
  activated. After that, every session receives PROJECT_CHANGED, and its subscriptions are cleared.

## 6. Link-loss safety rules (receiver)

1. **Momentary:** a MOMENTARY write holds the value for `hold_ms` (max 2000 ms). The panel
   re-sends it every `heartbeat_ms` while the button is held. When the hold expires, or the session that owns it
   closes, the receiver writes the tag's `release_value`.
2. **Fail-safe:** a tag with a `failsafe_value` is written to that value when the last
   *operator/engineer* session closes, or immediately on any session loss if
   `failsafe_on_any_loss = true`.
3. **PLC heartbeat:** a PLC connection can name a `heartbeat_tag` (DINT). While at least one
   operator/engineer session is connected, the receiver increments it every `heartbeat_ms`. The PLC
   program treats "no change for N × heartbeat_ms" as HMI loss (link down, receiver dead, or panel off) and
   puts the machine in a safe state. This is the recommended pattern: the PLC program owns the machine reaction.
4. **System tags** (`_System.Link.Clients`, `_System.Link.Connected`, `_System.PLC.<name>.Connected`, …)
   expose the receiver's diagnostics to panels.
