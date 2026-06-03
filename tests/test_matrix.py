from io import BytesIO
from pathlib import Path
from functools import partial

import httpx
import respx
import pytest
from pytest_mock import MockerFixture

pytest.importorskip("nonebot.adapters.matrix")
from nonebug import App
from nonebot import get_driver
from nonebot.adapters.matrix import Bot
from nonebot.adapters.matrix.config import BotInfo
from nonebot.adapters.matrix.api import WhoamiResponse

from .utils import assert_ms, mock_matrix_message_event

matrix_bot_info = BotInfo(homeserver="https://example.com", access_token="token")
matrix_self_info = WhoamiResponse(user_id="@bot:example.com")
matrix_kwargs = {
    "self_id": "@bot:example.com",
    "bot_info": matrix_bot_info,
    "self_info": matrix_self_info,
}


@pytest.fixture
def assert_matrix(app: App):
    from nonebot_plugin_saa import SupportedAdapters

    return partial(
        assert_ms,
        Bot,
        SupportedAdapters.matrix,
        **matrix_kwargs,
    )


async def test_text(app: App, assert_matrix):
    from nonebot.adapters.matrix import MessageSegment

    from nonebot_plugin_saa import Text

    await assert_matrix(app, Text("123"), MessageSegment.text("123"))


async def test_image_bytes(app: App, assert_matrix):
    from nonebot.adapters.matrix import MessageSegment

    from nonebot_plugin_saa import Image

    data = b"\x89PNG\r"
    await assert_matrix(
        app,
        Image(data),
        MessageSegment.image(content=b"\x89PNG\r", filename="image"),
    )


async def test_image_bytesio(app: App, assert_matrix):
    from nonebot.adapters.matrix import MessageSegment

    from nonebot_plugin_saa import Image

    data = BytesIO(b"\x89PNG\r")
    await assert_matrix(
        app,
        Image(data),
        MessageSegment.image(content=b"\x89PNG\r", filename="image"),
    )


async def test_image_path(app: App, assert_matrix, tmp_path: Path):
    from nonebot.adapters.matrix import MessageSegment

    from nonebot_plugin_saa import Image

    temp_image_path = tmp_path / "amiya.png"
    with temp_image_path.open("wb") as f:
        f.write(b"\x89PNG\r")

    await assert_matrix(
        app,
        Image(temp_image_path),
        MessageSegment.image(content=b"\x89PNG\r", filename=temp_image_path.name),
    )


@respx.mock
async def test_image_url(app: App, assert_matrix):
    from nonebot.adapters.matrix import MessageSegment

    from nonebot_plugin_saa import Image

    image_route = respx.get("https://example.com/amiya.png")
    image_route.mock(return_value=httpx.Response(200, content=b"amiya"))

    await assert_matrix(
        app,
        Image("https://example.com/amiya.png"),
        MessageSegment.image(content=b"amiya", filename="image"),
    )


@respx.mock
async def test_image_url_error(app: App, assert_matrix):
    from nonebot.adapters.matrix import MessageSegment

    from nonebot_plugin_saa import Image

    image_route = respx.get("https://example.com/amiya.png")
    image_route.mock(return_value=httpx.Response(404, content=b""))

    with pytest.raises(RuntimeError, match="Error downloading image"):
        await assert_matrix(
            app,
            Image("https://example.com/amiya.png"),
            MessageSegment.image(content=b"amiya", filename="image"),
        )


async def test_image_invalid_type(app: App, assert_matrix):
    from nonebot.adapters.matrix import MessageSegment

    from nonebot_plugin_saa import Image

    with pytest.raises(TypeError):
        await assert_matrix(
            app,
            Image(1),  # type: ignore
            MessageSegment.image(content=b"\x89PNG\r", filename="1.png"),
        )


async def test_mention(app: App, assert_matrix):
    from nonebot.adapters.matrix import MessageSegment

    from nonebot_plugin_saa import Mention

    await assert_matrix(
        app, Mention("@user:example.com"), MessageSegment.mention_user("@user:example.com")
    )


async def test_mention_all(app: App, assert_matrix):
    from nonebot.adapters.matrix import MessageSegment

    from nonebot_plugin_saa import MentionAll

    await assert_matrix(
        app,
        MentionAll(),
        MessageSegment.raw({"body": "@room", "msgtype": "m.text", "m.mentions": {"room": True}}),
    )


async def test_reply(app: App, assert_matrix):
    from nonebot.adapters.matrix import MessageSegment

    from nonebot_plugin_saa import Reply
    from nonebot_plugin_saa.adapters.matrix import MatrixMessageId

    await assert_matrix(
        app,
        Reply(MatrixMessageId(event_id="$abc:example.com", room_id="!room:example.com")),
        MessageSegment.reply(event_id="$abc:example.com"),
    )


async def test_extract_message_id(app: App, mocker: MockerFixture):
    from nonebot import on_message
    from nonebot.adapters.matrix import Bot
    from nonebot.adapters.matrix.event import RoomMessageEvent

    from nonebot_plugin_saa import Text, SupportedAdapters
    from nonebot_plugin_saa.registries import SaaMessageId
    from nonebot_plugin_saa.adapters.matrix import MatrixReceipt, MatrixMessageId

    mocker.patch(
        "nonebot.adapters.matrix.bot.make_txn_id", return_value="test_txn_0"
    )

    matcher = on_message()

    @matcher.handle()
    async def _(mid: SaaMessageId):
        assert mid == MatrixMessageId(
            event_id="$testevent123:example.com", room_id="!testroom:example.com"
        )

        receipt = await Text("amiya").send()
        assert isinstance(receipt, MatrixReceipt)
        assert receipt.extract_message_id() == MatrixMessageId(
            event_id="$testevent123:example.com", room_id="!testroom:example.com"
        )

    async with app.test_matcher(matcher) as ctx:
        adapter_obj = get_driver()._adapters[str(SupportedAdapters.matrix)]
        bot = ctx.create_bot(base=Bot, adapter=adapter_obj, **matrix_kwargs)
        msg_event = mock_matrix_message_event()
        ctx.receive_event(bot, msg_event)
        ctx.should_call_api(
            "send_message",
            data={
                "room_id": "!testroom:example.com",
                "event_type": "m.room.message",
                "content": {"msgtype": "m.text", "body": "amiya"},
                "txn_id": "test_txn_0",
            },
            result={"event_id": "$testevent123:example.com"},
        )


async def test_send(app: App, mocker: MockerFixture):
    from nonebot import on_message
    from nonebot.adapters.matrix import Bot

    from nonebot_plugin_saa import Text, MessageFactory, SupportedAdapters

    mocker.patch(
        "nonebot.adapters.matrix.bot.make_txn_id", return_value="test_txn_1"
    )

    matcher = on_message()

    @matcher.handle()
    async def process():
        await MessageFactory(Text("123")).send()

    async with app.test_matcher(matcher) as ctx:
        adapter_obj = get_driver()._adapters[str(SupportedAdapters.matrix)]
        bot = ctx.create_bot(base=Bot, adapter=adapter_obj, **matrix_kwargs)
        msg_event = mock_matrix_message_event()
        ctx.receive_event(bot, msg_event)
        ctx.should_call_api(
            "send_message",
            data={
                "room_id": "!testroom:example.com",
                "event_type": "m.room.message",
                "content": {"msgtype": "m.text", "body": "123"},
                "txn_id": "test_txn_1",
            },
            result={"event_id": "$sent123:example.com"},
        )


async def test_send_active(app: App, mocker: MockerFixture):
    from nonebot import get_driver
    from nonebot.adapters.matrix import Bot

    from nonebot_plugin_saa import Text, SupportedAdapters, TargetMatrixRoom

    mocker.patch(
        "nonebot.adapters.matrix.bot.make_txn_id", return_value="test_txn_2"
    )

    async with app.test_api() as ctx:
        adapter_obj = get_driver()._adapters[str(SupportedAdapters.matrix)]
        bot = ctx.create_bot(base=Bot, adapter=adapter_obj, **matrix_kwargs)

        send_target = TargetMatrixRoom(room_id="!room:example.com")

        ctx.should_call_api(
            "send_message",
            data={
                "room_id": "!room:example.com",
                "event_type": "m.room.message",
                "content": {"msgtype": "m.text", "body": "123"},
                "txn_id": "test_txn_2",
            },
            result={"event_id": "$sent456:example.com"},
        )
        await Text("123").send_to(send_target, bot)


async def test_receipt(app: App, mocker: MockerFixture):
    from nonebot import on_message
    from nonebot.adapters.matrix import Bot

    from nonebot_plugin_saa import Text, MessageFactory, SupportedAdapters
    from nonebot_plugin_saa.adapters.matrix import MatrixReceipt

    mocker.patch(
        "nonebot.adapters.matrix.bot.make_txn_id",
        side_effect=["test_send_txn", "test_revoke_txn"],
    )

    matcher = on_message()

    @matcher.handle()
    async def process():
        receipt = await MessageFactory(Text("123")).send()
        assert isinstance(receipt, MatrixReceipt)
        assert receipt.raw == {
            "event_id": "$sent:example.com",
            "room_id": "!testroom:example.com",
        }
        await receipt.revoke(reason="test")

    async with app.test_matcher(matcher) as ctx:
        adapter_obj = get_driver()._adapters[str(SupportedAdapters.matrix)]
        bot = ctx.create_bot(base=Bot, adapter=adapter_obj, **matrix_kwargs)
        msg_event = mock_matrix_message_event()
        ctx.receive_event(bot, msg_event)
        ctx.should_call_api(
            "send_message",
            data={
                "room_id": "!testroom:example.com",
                "event_type": "m.room.message",
                "content": {"msgtype": "m.text", "body": "123"},
                "txn_id": "test_send_txn",
            },
            result={"event_id": "$sent:example.com"},
        )
        ctx.should_call_api(
            "redact_event",
            data={
                "room_id": "!testroom:example.com",
                "event_id": "$sent:example.com",
                "txn_id": "test_revoke_txn",
                "reason": "test",
            },
            result={"event_id": "$redact:example.com"},
        )


async def test_convert_to_arg(app: App):
    from nonebot import get_driver
    from nonebot.adapters.matrix import Bot

    from nonebot_plugin_saa import SupportedAdapters, TargetMatrixRoom

    async with app.test_api() as ctx:
        adapter_obj = get_driver()._adapters[str(SupportedAdapters.matrix)]
        bot = ctx.create_bot(base=Bot, adapter=adapter_obj, **matrix_kwargs)
        target = TargetMatrixRoom(room_id="!room:example.com")
        assert target.arg_dict(bot) == {"room_id": "!room:example.com"}


async def test_target_extraction(app: App):
    from nonebot_plugin_saa import SupportedAdapters, TargetMatrixRoom
    from nonebot_plugin_saa.registries import extract_target

    event = mock_matrix_message_event()
    target = extract_target(event)
    assert target == TargetMatrixRoom(room_id="!testroom:example.com")
    assert target.arg_dict({SupportedAdapters.matrix: None}) == {
        "room_id": "!testroom:example.com",
    }
