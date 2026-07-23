from __future__ import annotations

import json

import pytest

from jenai.bridge._wire import (
    EventFrame,
    RequestFrame,
    ResponseFrame,
    WireProtocolError,
    decode_frame,
    decode_request,
    encode_request,
)


def test_request_encoding_keeps_protocol_fields_authoritative() -> None:
    encoded = encode_request(7, "pose", {"timeout": 2.5})

    assert json.loads(encoded) == {"id": 7, "op": "pose", "timeout": 2.5}
    assert encoded.endswith(b"\n")


@pytest.mark.parametrize("reserved", ["id", "op"])
def test_request_encoding_rejects_reserved_params(reserved: str) -> None:
    with pytest.raises(WireProtocolError, match="reserved"):
        encode_request(1, "ping", {reserved: "overridden"})


@pytest.mark.parametrize("request_id", [0, -1, True])
def test_request_encoding_rejects_invalid_ids(request_id: int) -> None:
    with pytest.raises(WireProtocolError, match="positive integer"):
        encode_request(request_id, "ping")


def test_frame_decoding_returns_typed_events_and_responses() -> None:
    event = decode_frame(b'{"event":"ready","generation":2}\n')
    success = decode_frame(b'{"id":4,"ok":true,"result":{"pong":true}}\n')
    failure = decode_frame(b'{"id":5,"ok":false,"error":"offline"}\n')

    assert event == EventFrame("ready", {"event": "ready", "generation": 2})
    assert success == ResponseFrame(4, True, {"pong": True})
    assert failure == ResponseFrame(5, False, {}, "offline")


def test_non_protocol_output_is_ignored_but_invalid_frames_fail_fast() -> None:
    assert decode_frame(b"an rclpy diagnostic\n") is None
    assert decode_frame(b"[]\n") is None

    with pytest.raises(WireProtocolError, match="response id"):
        decode_frame(b'{"id":"wrong","ok":true,"result":{}}\n')
    with pytest.raises(WireProtocolError, match="result must be an object"):
        decode_frame(b'{"id":1,"ok":true,"result":[]}\n')


def test_request_decoding_separates_protocol_fields_from_params() -> None:
    request = decode_request('{"id":9,"op":"nav_send","x":1.5,"tag":"a"}\n')

    assert request == RequestFrame(9, "nav_send", {"x": 1.5, "tag": "a"})


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        "[]",
        '{"id":true,"op":"ping"}',
        '{"id":-1,"op":"ping"}',
        '{"id":1,"op":""}',
    ],
)
def test_request_decoding_rejects_malformed_contracts(payload: str) -> None:
    with pytest.raises(WireProtocolError):
        decode_request(payload)
