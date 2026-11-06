"""Shared bounded inference contract. Author: FubukiHanasaki."""

import base64
import re
import struct
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_MEDIA_BYTES = 2 * 1024 * 1024
MAX_INPUT_BYTES = 12 * 1024 * 1024
IMAGE_SIZES = {"256x256", "512x512", "768x768", "1024x1024", "1536x1024", "1024x1536"}


class Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)
    context_length: int = Field(default=8192, ge=512, le=262144)
    max_tokens: int = Field(default=2048, ge=1, le=32768)
    temperature: float = Field(default=0.7, ge=0, le=2)
    top_p: float = Field(default=0.8, gt=0, le=1)
    top_k: int = Field(default=20, ge=1, le=200)
    repetition_penalty: float = Field(default=1.0, ge=1, le=2)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)
    thinking: bool = False
    kv_cache_type: Literal[
        "f16", "q8_0", "q4_0", "bf16", "fp8", "int8", "nvfp4", "k8v4"
    ] = "f16"
    kv_capacity_mb: int = Field(default=0, ge=0, le=65536)
    prefix_reuse: bool = True
    flash_attention: bool = True
    vision: bool = False

    @model_validator(mode="after")
    def capacity(self):
        if self.max_tokens >= self.context_length:
            raise ValueError("输出上限必须小于上下文长度")
        return self


class ImageParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)
    steps: int = Field(default=20, ge=1, le=100)
    cfg: float = Field(default=4.0, ge=0, le=30)
    seed: int = Field(default=0, ge=0, le=2**53 - 1)
    default_size: Literal[
        "256x256", "512x512", "768x768", "1024x1024", "1536x1024", "1024x1536"
    ] = "512x512"


def parameters(value=None, adapter="ollama"):
    result = Parameters(**(value or {})).model_dump()
    if adapter == "ninfer":
        if result["kv_cache_type"] not in {"bf16", "fp8", "int8", "nvfp4", "k8v4"}:
            raise ValueError("NInfer KV 类型需要 bf16、fp8、int8、nvfp4 或 k8v4")
        if result["top_k"] > 20:
            raise ValueError("NInfer top_k 最大为 20")
        if result["repetition_penalty"] != 1.0:
            raise ValueError("NInfer 不支持 repetition_penalty")
    elif adapter == "ollama" and result["kv_cache_type"] not in {"f16", "q8_0", "q4_0"}:
        raise ValueError("Ollama KV 类型需要 f16、q8_0 或 q4_0")
    if not result["flash_attention"] and result["kv_cache_type"] in {"q8_0", "q4_0"}:
        raise ValueError("量化 KV 缓存需要 Flash Attention")
    return result


def image_bytes(url):
    if not isinstance(url, str) or len(url) > MAX_MEDIA_BYTES * 4 // 3 + 128:
        raise ValueError("图片过大，单张最大 2 MB")
    match = re.fullmatch(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)", url)
    if not match:
        raise ValueError("仅接收 PNG、JPEG、WebP 的内嵌图片，不读取外部地址")
    data = base64.b64decode(match[2], validate=True)
    if not data or len(data) > MAX_MEDIA_BYTES:
        raise ValueError("图片过大或为空")
    valid = (
        (match[1] == "png" and data.startswith(b"\x89PNG\r\n\x1a\n"))
        or (match[1] == "jpeg" and data.startswith(b"\xff\xd8\xff"))
        or (match[1] == "webp" and data.startswith(b"RIFF") and data[8:12] == b"WEBP")
    )
    if not valid:
        raise ValueError("图片内容与格式不一致")
    width = height = 0
    try:
        if match[1] == "png" and data[12:16] == b"IHDR":
            width, height = struct.unpack(">II", data[16:24])
        elif match[1] == "webp":
            if data[12:16] == b"VP8X":
                width, height = (
                    1 + int.from_bytes(data[24:27], "little"),
                    1 + int.from_bytes(data[27:30], "little"),
                )
            elif data[12:16] == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
                width, height = (v & 0x3FFF for v in struct.unpack("<HH", data[26:30]))
            elif data[12:16] == b"VP8L" and data[20] == 0x2F:
                bits = int.from_bytes(data[21:25], "little")
                width, height = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        elif match[1] == "jpeg":
            offset = 2
            while offset + 4 < len(data):
                if data[offset] != 0xFF:
                    break
                marker = data[offset + 1]
                offset += 2
                if marker in {0xD8, 0xD9}:
                    continue
                length = int.from_bytes(data[offset : offset + 2], "big")
                if marker in {0xC0, 0xC1, 0xC2}:
                    height, width = struct.unpack(">HH", data[offset + 3 : offset + 7])
                    break
                if length < 2:
                    break
                offset += length
    except (IndexError, struct.error):
        raise ValueError("图片头无效") from None
    if not 0 < width <= 8192 or not 0 < height <= 8192 or width * height > 16_000_000:
        raise ValueError("图片尺寸无效或超过 1600 万像素")
    return data


def validate_messages(messages, vision=False):
    if not isinstance(messages, list) or not 1 <= len(messages) <= 64:
        raise ValueError("对话需要 1–64 条消息")
    images = 0
    for msg in messages:
        if (
            not isinstance(msg, dict)
            or set(msg) != {"role", "content"}
            or msg["role"] not in {"system", "user", "assistant"}
        ):
            raise ValueError("消息格式无效")
        content = msg["content"]
        if isinstance(content, str):
            if len(content) > (262144 if msg["role"] == "assistant" else 16000):
                raise ValueError("单条文字最多 16000 字符")
        elif (
            isinstance(content, list)
            and 1 <= len(content) <= 17
            and msg["role"] == "user"
        ):
            for part in content:
                if not isinstance(part, dict):
                    raise ValueError("多模态内容格式无效")
                if (
                    part.get("type") == "text"
                    and set(part) == {"type", "text"}
                    and isinstance(part["text"], str)
                    and len(part["text"]) <= 16000
                ):
                    continue
                if (
                    vision
                    and part.get("type") == "image_url"
                    and set(part) == {"type", "image_url"}
                    and isinstance(part["image_url"], dict)
                    and set(part["image_url"]) == {"url"}
                ):
                    image_bytes(part["image_url"]["url"])
                    images += 1
                    continue
                raise ValueError("此模型未启用视觉能力或内容格式不支持")
        else:
            raise ValueError("消息内容无效")
    if images > 8:
        raise ValueError("单次上下文最多 8 张图片或视频采样帧")
    return messages


def ollama_messages(messages):
    result = []
    for msg in messages:
        if isinstance(msg["content"], str):
            result.append(msg)
        else:
            result.append(
                {
                    "role": msg["role"],
                    "content": "\n".join(
                        p["text"] for p in msg["content"] if p["type"] == "text"
                    ),
                    "images": [
                        p["image_url"]["url"].split(",", 1)[1]
                        for p in msg["content"]
                        if p["type"] == "image_url"
                    ],
                }
            )
    return result


def image_seed(requested, default):
    """Resolve only the seed; resource/model settings remain administrator-owned."""
    value = default if requested is None else requested
    if type(value) is not int or not 0 <= value <= 2**53 - 1:
        raise ValueError("图片 seed 必须是 0–9007199254740991 的整数或 null")
    return value


def validate_negative_prompt(value):
    if value is not None and (not isinstance(value, str) or len(value) > 2000):
        raise ValueError("负向提示词必须是最多 2000 字符的字符串或 null")
    return value
