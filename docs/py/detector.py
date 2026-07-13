"""cta1_*.img 形式の検出器画像を読み込むモジュール。

バイナリ読み込み→16bitデコード→reshape→転置をNumPyでベクトル化して行う。

ブラウザ内実行(Pyodide)では File API で読み込んだバイト列をそのまま渡すため、
ファイルパスではなく bytes を受け取る。
"""
from __future__ import annotations

import numpy as np


def load_detector_image(rawdata: bytes, img_size: int = 4000, img_bit: int = 16) -> tuple[np.ndarray, int]:
    """.img ファイルのバイト列を読み込み、(img, header_bytes) を返す。

    16bitワードの最上位ビットが1の画素は、下位15bitの値を32倍する
    （このフォーマット固有の仕様。5bitシフト相当で×32が正しい）。
    """
    img_size_byte = int((img_size ** 2) * (img_bit / 8))
    header = int(len(rawdata) - img_size_byte)
    if header < 0:
        raise ValueError(
            f"ファイルサイズがimg_size/img_bitと合いません: "
            f"file={len(rawdata)} bytes, expected image bytes={img_size_byte}"
        )

    body = np.frombuffer(rawdata, dtype=np.uint8, offset=header)
    high = body[0::2].astype(np.uint32)
    low = body[1::2].astype(np.uint32)
    val16 = (high << 8) | low

    msb = (val16 >> 15) & 1
    rem15 = val16 & 0x7FFF
    decoded = np.where(msb == 0, rem15, rem15 * 32)

    img = decoded.reshape(img_size, img_size)
    img = img.T
    return img, header
