"""Protocolo TCP entre el A53 (FreeRTOS, ethernet.c) y el scope.

Placa -> PC: paquetes ``NetworkSendStruct`` (little endian, sin padding)::

    uint32 status
    float  slowDataContent[15]
    float  val_01[15] ... val_20[15]      (20 canales x 15 muestras)
    float  slowDataID[15]

PC -> placa: tras CADA paquete recibido el PC debe contestar (la placa hace un
``read`` bloqueante). La respuesta es ``APU_to_RPU_t``: ``uint32 id`` + ``float value``.
``id == 0`` no hace nada; el resto se despacha en ``ipc_ARM.c`` del R5.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

CHANNELS = 20                 # JS_CHANNELS
SAMPLES_PER_PACKET = 15       # NETWORK_SEND_FIELD_SIZE
DEFAULT_PORT = 1000           # TCPPORT
DEFAULT_HOST = "192.168.1.233"

PACKET_SIZE = 4 + 4 * SAMPLES_PER_PACKET * (CHANNELS + 2)   # 1324 bytes
COMMAND_STRUCT = struct.Struct("<If")
COMMAND_SIZE = COMMAND_STRUCT.size                           # 8 bytes

# Ids de comando fijos (primeras entradas del enum gui_button_mapping de javascope.h). Los ids de los
# botones de usuario (My_Button_n) y de los campos (Set_Send_Field_n) se leen del header (BoardNames).
CMD_NONE = 0
CMD_ENABLE_SYSTEM = 1
CMD_ENABLE_CONTROL = 2
CMD_STOP = 3
CMD_ERROR_RESET = 4
CMD_SELECT_CHANNEL_1 = 201    # ... 220: value = indice del enum JS_OberservableData

# Bits de status (js_status_BareToRTOS en ipc_ARM.c)
STATUS_LED_READY = 0
STATUS_LED_RUNNING = 1
STATUS_LED_ERROR = 2
STATUS_LED_USER = 3
STATUS_MY_BUTTON_BASE = 4     # bit 4 + n - 1 -> indicador de My_Button_n

_dtype = np.dtype([
    ("status", "<u4"),
    ("slow_content", "<f4", (SAMPLES_PER_PACKET,)),
    ("values", "<f4", (CHANNELS, SAMPLES_PER_PACKET)),
    ("slow_id", "<f4", (SAMPLES_PER_PACKET,)),
])
assert _dtype.itemsize == PACKET_SIZE


@dataclass
class Packet:
    status: int
    samples: np.ndarray        # (SAMPLES_PER_PACKET, CHANNELS) float32
    slow_id: np.ndarray        # (SAMPLES_PER_PACKET,) int
    slow_content: np.ndarray   # (SAMPLES_PER_PACKET,) float32


def parse_packet(data: bytes) -> Packet:
    """Convierte los 1324 bytes de un NetworkSendStruct en arrays numpy."""
    rec = np.frombuffer(data, dtype=_dtype, count=1)[0]
    return Packet(
        status=int(rec["status"]),
        samples=np.ascontiguousarray(rec["values"].T),
        slow_id=np.rint(rec["slow_id"]).astype(np.int64),
        slow_content=rec["slow_content"].copy(),
    )


def build_command(cmd_id: int, value: float = 0.0) -> bytes:
    return COMMAND_STRUCT.pack(int(cmd_id), float(value))


def build_packet(status: int, samples: np.ndarray, slow_id: np.ndarray, slow_content: np.ndarray) -> bytes:
    """Inverso de parse_packet (usado por el simulador de placa)."""
    rec = np.zeros(1, dtype=_dtype)
    rec["status"] = status
    rec["values"][0] = np.asarray(samples, dtype=np.float32).T
    rec["slow_id"][0] = np.asarray(slow_id, dtype=np.float32)
    rec["slow_content"][0] = np.asarray(slow_content, dtype=np.float32)
    return rec.tobytes()
