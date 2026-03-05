import struct

# ---------- constants ----------
MAGIC_U16 = 0xA55A
MAGIC = struct.pack("<H", MAGIC_U16)

CMD_PING = 0x00
CMD_ARM_SINGLE = 0x01
CMD_STOP = 0x02

# ---------- headers ----------
CMD_HDR_FMT = "<BBH"  # cmd, flags, length
FRAME_HDR_FMT = "<HBHBBBQ"  # magic, header_size, sample_count, trigger, ch_config, time_config, reserved

CMD_HDR_SIZE = struct.calcsize(CMD_HDR_FMT)
FRAME_HDR_SIZE = struct.calcsize(FRAME_HDR_FMT)

FRAME_HEADER_BYTES = 16
CHANNEL_COUNT = 2
SAMPLE_BYTES_PER_CHANNEL = 2
PAYLOAD_BYTES_PER_SAMPLE = CHANNEL_COUNT * SAMPLE_BYTES_PER_CHANNEL

CH_CONFIG_CH1_EN = 1 << 7
CH_CONFIG_CH1_VDIV_POS = 4
CH_CONFIG_CH1_VDIV_MASK = 0x70
CH_CONFIG_CH2_EN = 1 << 3
CH_CONFIG_CH2_VDIV_POS = 0
CH_CONFIG_CH2_VDIV_MASK = 0x07

TIME_CONFIG_SDIV_POS = 3
TIME_CONFIG_SDIV_MASK = 0xF8
TIME_CONFIG_DEC_POS = 0
TIME_CONFIG_DEC_MASK = 0x07

FRAME_META_TRIGGER_INDEX_MASK = 0x0000FFFF
FRAME_META_TRIGGER_FOUND_BIT = 1 << 16
FRAME_META_TRIGGER_SRC_CH2_BIT = 1 << 17
FRAME_META_SPAN_POS = 32
FRAME_META_SPAN_MASK = 0x0000FFFF << FRAME_META_SPAN_POS

V_DIV_OPTIONS_V = (0.10, 0.20, 0.50, 1.00, 2.00, 5.00, 10.00)
S_DIV_OPTIONS_S = (
    5e-6,
    10e-6,
    20e-6,
    50e-6,
    100e-6,
    200e-6,
    500e-6,
    1e-3,
    2e-3,
    5e-3,
    10e-3,
    20e-3,
    50e-3,
    100e-3,
)


def to_millivolts(volts: float) -> int:
    return int(round(volts * 1000.0))


def to_microseconds(seconds: float) -> int:
    return int(round(seconds * 1_000_000.0))


def encode_set_ch1_vdiv(v_div_volts: float) -> bytes:
    return f"SET CH1_VDIV_MV {to_millivolts(v_div_volts)}\n".encode("ascii")


def encode_set_ch2_vdiv(v_div_volts: float) -> bytes:
    return f"SET CH2_VDIV_MV {to_millivolts(v_div_volts)}\n".encode("ascii")


def encode_set_sdiv(s_div_seconds: float) -> bytes:
    return f"SET SDIV_US {to_microseconds(s_div_seconds)}\n".encode("ascii")
