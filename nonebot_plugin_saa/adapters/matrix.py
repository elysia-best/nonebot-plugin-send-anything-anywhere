from io import BytesIO
from pathlib import Path
from functools import partial
from typing import Any, Literal, Optional, cast

from nonebot.adapters import Event
from nonebot.drivers import Request
from nonebot.adapters import Bot as BaseBot
from nonebot.adapters.matrix import Bot as BotMatrix
from nonebot.adapters.matrix import Message, MessageSegment
from nonebot.adapters.matrix.event import MessageEvent, RoomMessageEvent

from ..types import Text, Image, Reply, Mention, MentionAll
from ..utils import SupportedAdapters, SupportedPlatform, type_message_id_check
from ..abstract_factories import (
    MessageFactory,
    register_ms_adapter,
    assamble_message_factory,
)
from ..registries import (
    Receipt,
    MessageId,
    PlatformTarget,
    TargetMatrixRoom,
    register_sender,
    register_convert_to_arg,
    register_target_extractor,
    register_message_id_getter,
)

adapter = SupportedAdapters.matrix
register_matrix = partial(register_ms_adapter, adapter)

MessageFactory.register_adapter_message(SupportedAdapters.matrix, Message)


class MatrixMessageId(MessageId):
    adapter_name: Literal[SupportedAdapters.matrix] = adapter
    event_id: str
    room_id: str


@register_message_id_getter(MessageEvent)
def _get_msg_id(event: Event) -> MatrixMessageId:
    assert isinstance(event, MessageEvent)
    assert event.event_id is not None, "Matrix MessageEvent must have event_id"
    return MatrixMessageId(
        event_id=str(event.event_id),
        room_id=str(event.room_id),
    )


@register_matrix(Text)
def _text(t: Text) -> MessageSegment:
    return MessageSegment.text(t.data["text"])


@register_matrix(Image)
async def _image(i: Image, bot: BaseBot) -> MessageSegment:
    if not isinstance(bot, BotMatrix):
        raise TypeError(f"Unsupported type of bot: {type(bot)}")
    image = i.data["image"]
    image_name = i.data["name"]

    if isinstance(image, Path) and image.is_file():
        with image.open("rb") as f:
            img_bytes = f.read()
        image_name = image.name

    elif isinstance(image, str):
        req = Request("GET", image, timeout=10)
        resp = await bot.adapter.request(req)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Error downloading image, status code: {resp.status_code}, url: {image}"
            )
        img_bytes = resp.content
        if not isinstance(img_bytes, bytes):
            raise TypeError(f"Expected bytes, got something else {type(img_bytes)}")

    elif isinstance(image, bytes):
        img_bytes = image

    elif isinstance(image, BytesIO):
        img_bytes = image.getvalue()

    else:
        raise TypeError(f"Invalid image type {type(image)}")

    return MessageSegment.image(content=img_bytes, filename=image_name)


@register_matrix(Reply)
def _reply(r: Reply) -> MessageSegment:
    mid = type_message_id_check(MatrixMessageId, r.data["message_id"])
    return MessageSegment.reply(event_id=mid.event_id)


@register_matrix(Mention)
def _mention(m: Mention) -> MessageSegment:
    return MessageSegment.mention_user(user_id=m.data["user_id"])


@register_matrix(MentionAll)
def _mention_all(m: MentionAll) -> MessageSegment:
    return MessageSegment.raw(
        {
            "body": "@room",
            "msgtype": "m.text",
            "m.mentions": {"room": True},
        }
    )


@register_target_extractor(MessageEvent)
@register_target_extractor(RoomMessageEvent)
def _extract_msg_event(event: Event) -> TargetMatrixRoom:
    assert isinstance(event, MessageEvent)
    return TargetMatrixRoom(room_id=str(event.room_id))


@register_convert_to_arg(adapter, SupportedPlatform.matrix_room)
def _gen_room(target: PlatformTarget) -> dict[str, Any]:
    assert isinstance(target, TargetMatrixRoom)
    return {
        "room_id": target.room_id,
    }


class MatrixReceipt(Receipt):
    adapter_name: SupportedAdapters = adapter
    event_id: str
    room_id: str

    async def revoke(self, reason: Optional[str] = None):
        return await cast(BotMatrix, self._get_bot()).redact(
            room_id=self.room_id,
            event_id=self.event_id,
            reason=reason,
        )

    @property
    def raw(self) -> dict[str, str]:
        return {"event_id": self.event_id, "room_id": self.room_id}

    def extract_message_id(self) -> MatrixMessageId:
        return MatrixMessageId(event_id=self.event_id, room_id=self.room_id)


@register_sender(adapter)
async def send(
    bot,
    msg: MessageFactory,
    target,
    event,
    at_sender: bool,
    reply: bool,
) -> MatrixReceipt:
    assert isinstance(bot, BotMatrix)
    assert isinstance(target, TargetMatrixRoom)
    if event:
        assert isinstance(event, MessageEvent)
        full_msg = assamble_message_factory(
            msg,
            Mention(event.get_user_id()),
            Reply(
                MatrixMessageId(
                    event_id=str(event.event_id),
                    room_id=str(event.room_id),
                )
            ),
            at_sender,
            reply,
        )
    else:
        full_msg = msg
    message_to_send = Message()
    for message_segment_factory in full_msg:
        message_segment = await message_segment_factory.build(bot)
        message_to_send += message_segment
    resp = await bot.send_to(message=message_to_send, **target.arg_dict(bot))
    return MatrixReceipt(
        event_id=str(resp.event_id),
        room_id=target.room_id,
        bot_id=bot.self_id,
    )
