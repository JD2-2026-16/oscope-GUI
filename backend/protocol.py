import struct

# ---------- constants ----------
MAGIC = b"SCP0"

CMD_PING = 0x00
CMD_ARM_SINGLE = 0x01
CMD_STOP = 0x02

# ---------- headers ----------
CMD_HDR_FMT = "<BBH"  # cmd, flags, length
FRAME_HDR_FMT = "<4sIHHIII"  # magic, rate, chans, bits, count, trig, reserved

CMD_HDR_SIZE = struct.calcsize(CMD_HDR_FMT)
FRAME_HDR_SIZE = struct.calcsize(FRAME_HDR_FMT)
