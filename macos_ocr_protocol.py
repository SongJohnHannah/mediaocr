"""WeChat 4.x OCR wire messages (shared field numbers on macOS/Windows/Linux).

Only fields consumed by mediaocr are described; protobuf skips other fields.
The IPC contract is documented by https://github.com/swigger/wechat-ocr/tree/master/pb.
"""

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory


def _messages():
    schema = descriptor_pb2.FileDescriptorProto(
        name="mediaocr_macos.proto", package="mediaocr", syntax="proto2"
    )
    # name, field number, protobuf type, repeated, message type
    definitions = {
        "Support": [("supported", 1, 8, False, None)],
        "Options": [("t1", 1, 8, False, None), ("t2", 2, 8, False, None),
                    ("t3", 3, 8, False, None)],
        "Request": [("task_id", 1, 4, False, None), ("pic_path", 2, 9, False, None),
                    ("options", 6, 11, False, "Options")],
        "Line": [("text", 2, 9, False, None), ("rate", 3, 2, False, None),
                 ("left", 5, 2, False, None), ("top", 6, 2, False, None),
                 ("right", 7, 2, False, None), ("bottom", 8, 2, False, None)],
        "Result": [("lines", 3, 11, True, "Line"), ("width", 4, 13, False, None),
                   ("height", 5, 13, False, None)],
        "Response": [("task_id", 1, 4, False, None), ("errcode", 2, 5, False, None),
                     ("result", 3, 11, False, "Result")],
    }
    for name, fields in definitions.items():
        message = schema.message_type.add(name=name)
        for field_name, number, kind, repeated, target in fields:
            field = message.field.add(
                name=field_name, number=number, type=kind,
                label=3 if repeated else 1,
            )
            if target:
                field.type_name = f".mediaocr.{target}"
    pool = descriptor_pool.DescriptorPool()
    pool.Add(schema)
    return {name: message_factory.GetMessageClass(
        pool.FindMessageTypeByName(f"mediaocr.{name}")) for name in definitions}


_TYPES = _messages()
Support = _TYPES["Support"]
Request = _TYPES["Request"]
Response = _TYPES["Response"]


def encode_request(task_id: int, image_path: str) -> bytes:
    request = Request(task_id=task_id, pic_path=image_path)
    request.options.t1 = True
    request.options.t2 = True
    request.options.t3 = False
    return request.SerializeToString()


def decode_response(data: bytes) -> tuple[int, dict]:
    response = Response.FromString(data)
    code = response.errcode
    if not response.HasField("result") and code == 0:
        code = -1
    result = {
        "errcode": code,
        "width": response.result.width,
        "height": response.result.height,
        "ocr_response": [
            {key: getattr(line, key) for key in
             ("text", "rate", "left", "top", "right", "bottom")}
            for line in response.result.lines
        ],
    }
    if code:
        result["error"] = f"微信 macOS OCR 识别失败（errcode={code}）"
    return response.task_id, result
