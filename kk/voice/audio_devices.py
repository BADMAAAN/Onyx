from __future__ import annotations

from typing import Any

import sounddevice as sd


def list_audio_devices() -> list[dict[str, Any]]:
    default_input, default_output = sd.default.device
    result = []
    for index, device in enumerate(sd.query_devices()):
        result.append(
            {
                "index": index,
                "name": device["name"],
                "input_channels": device["max_input_channels"],
                "output_channels": device["max_output_channels"],
                "default_samplerate": device["default_samplerate"],
                "default_input": index == default_input,
                "default_output": index == default_output,
                "hostapi": sd.query_hostapis(device["hostapi"])["name"],
            }
        )
    return result


def find_device(name_or_index: str | int | None, *, input_device: bool) -> int | None:
    if name_or_index is None or name_or_index == "":
        return None
    if isinstance(name_or_index, int) or str(name_or_index).isdigit():
        return int(name_or_index)
    needle = str(name_or_index).casefold()
    identity=needle[1:].split('|',1) if needle.startswith('@') else None
    channel_key = "max_input_channels" if input_device else "max_output_channels"
    matches = []
    default_input, default_output = sd.default.device
    for index, item in enumerate(sd.query_devices()):
        if (needle in item["name"].casefold() if identity is None else identity == [sd.query_hostapis(item["hostapi"])["name"].casefold(),item["name"].casefold()]) and item[channel_key] > 0:
            host = sd.query_hostapis(item["hostapi"])["name"]
            if input_device:
                # Prefer the user's Windows default endpoint when the configured
                # name matches.  Focusrite exposes the same input through MME,
                # DirectSound and WASAPI; DirectSound was not the most reliable
                # endpoint on the acceptance machine.
                priority = (-1 if index == default_input else
                            0 if "WASAPI" in host else
                            1 if "MME" in host else 2)
            else:
                priority = (-1 if index == default_output else
                            0 if "WASAPI" in host else 1 if "DirectSound" in host else 2)
            matches.append((priority, index))
    if not matches:
        raise LookupError(f"Audio device not found: {name_or_index}")
    return min(matches)[1]
