"""DJI DUML checksums.

Reflected (LSB-first) implementations, parameters from the osmosis protocol
reference (MEDIA_PROTOCOL.md §3):
  CRC8:  init 0x77, poly 0x8C   (== spec init 0xEE poly 0x31 refin/refout)
  CRC16: init 0x3692, poly 0x8408 (== spec init 0x496C poly 0x1021 refin/refout)
"""


def crc8(data: bytes) -> int:
    crc = 0x77
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8C if crc & 1 else crc >> 1
    return crc


def crc16(data: bytes) -> int:
    crc = 0x3692
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc
