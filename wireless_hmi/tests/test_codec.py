import math

import pytest

from whmi.common.codes import Quality, Role
from whmi.common.datatypes import DataType
from whmi.link.codec import ProtocolError, Reader, Writer
from whmi.link.frames import HEADER_SIZE, MAX_PLAINTEXT, FrameHeader
from whmi.link.messages import (
    SessionInfo,
    TagValue,
    decode_data_update,
    decode_id_list,
    decode_write_req,
    encode_data_updates,
    encode_id_list,
    encode_write_req,
)


@pytest.mark.parametrize("dtype,value", [
    (DataType.BOOL, True), (DataType.SINT, -5), (DataType.INT, -32768), (DataType.DINT, 2**31 - 1),
    (DataType.LINT, -(2**63)), (DataType.USINT, 255), (DataType.UINT, 65535), (DataType.UDINT, 2**32 - 1),
    (DataType.ULINT, 2**64 - 1), (DataType.REAL, 1.5), (DataType.LREAL, math.pi), (DataType.STRING, "Grüße"),
])
def test_value_roundtrip(dtype, value):
    data = Writer().value(dtype, value).getvalue()
    assert data[0] == int(dtype)
    r = Reader(data)
    assert r.value() == (dtype, value)
    r.done()


def test_reader_bounds_and_garbage():
    with pytest.raises(ProtocolError):
        Reader(b"\x01").u16()
    with pytest.raises(ProtocolError):
        Reader(b"\x99\x00").value()  # unknown type code
    with pytest.raises(ProtocolError):
        Reader(bytes([0xC1, 2])).value()  # BOOL must be 0/1
    with pytest.raises(ProtocolError):
        Reader(b"\x02\xff\xfe").str8()  # invalid UTF-8
    with pytest.raises(ProtocolError):
        Reader(b"\x00\x00").done()


def test_header_validation():
    h = FrameHeader(0x20, 1, 7, 100).pack()
    assert len(h) == HEADER_SIZE
    assert FrameHeader.unpack(h).seq == 7
    with pytest.raises(ProtocolError):
        FrameHeader.unpack(b"XX" + h[2:])
    with pytest.raises(ProtocolError):
        FrameHeader.unpack(FrameHeader(0x20, 0x02, 1, 0).pack())  # reserved flag
    with pytest.raises(ProtocolError):
        FrameHeader.unpack(FrameHeader(0x20, 1, 1, 20000).pack())  # too long


def test_data_update_split_respects_frame_size():
    values = [TagValue(i, Quality.GOOD, 1, DataType.STRING, "x" * 80) for i in range(1, 1001)]
    payloads = encode_data_updates(values)
    assert len(payloads) > 1
    assert all(len(p) <= MAX_PLAINTEXT for p in payloads)
    decoded = [v for p in payloads for v in decode_data_update(p)]
    assert [v.tag_id for v in decoded] == list(range(1, 1001))


def test_request_roundtrips():
    payload = encode_id_list(9, [1, 2, 3], interval_ms=50)
    assert decode_id_list(payload, with_interval=True, limit=10) == (9, 50, [1, 2, 3])
    with pytest.raises(ProtocolError):
        decode_id_list(encode_id_list(1, range(11)), limit=10)
    req = encode_write_req(4, [(1, DataType.REAL, 2.5), (2, DataType.BOOL, True)])
    assert decode_write_req(req) == (4, [(1, DataType.REAL, 2.5), (2, DataType.BOOL, True)])


def test_session_info_roundtrip():
    si = SessionInfo(Role.OPERATOR, 250, 1000, bytes(range(32)), 1234)
    assert SessionInfo.decode(si.encode()) == si
