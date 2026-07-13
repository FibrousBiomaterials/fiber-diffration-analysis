"""cta1_*.img 形式および TIFF 形式の検出器画像を読み込むモジュール。

.img はバイナリ読み込み→16bitデコード→reshape→転置をNumPyでベクトル化して行う。
TIFF は Pillow でデコードする。

ブラウザ内実行(Pyodide)では File API で読み込んだバイト列をそのまま渡すため、
ファイルパスではなく bytes を受け取る。
"""
from __future__ import annotations

import io

import numpy as np
from PIL import Image


def load_detector_image(
    rawdata: bytes, img_width: int = 4000, img_height: int = 4000, img_bit: int = 16
) -> tuple[np.ndarray, int]:
    """.img ファイルのバイト列を読み込み、(img, header_bytes) を返す。戻り値の img.shape は (img_height, img_width)。

    16bitワードの最上位ビットが1の画素は、下位15bitの値を32倍する
    （このフォーマット固有の仕様。5bitシフト相当で×32が正しい）。
    """
    img_size_byte = int(img_width * img_height * (img_bit / 8))
    header = int(len(rawdata) - img_size_byte)
    if header < 0:
        raise ValueError(
            f"ファイルサイズがimg_width/img_height/img_bitと合いません: "
            f"file={len(rawdata)} bytes, expected image bytes={img_size_byte}"
        )

    body = np.frombuffer(rawdata, dtype=np.uint8, offset=header)
    high = body[0::2].astype(np.uint32)
    low = body[1::2].astype(np.uint32)
    val16 = (high << 8) | low

    msb = (val16 >> 15) & 1
    rem15 = val16 & 0x7FFF
    decoded = np.where(msb == 0, rem15, rem15 * 32)

    img = decoded.reshape(img_width, img_height)
    img = img.T
    return img, header


def load_tiff_image(rawdata: bytes) -> tuple[np.ndarray, int]:
    """TIFFファイルのバイト列を読み込み、(img, header_bytes) を返す。

    正方形である必要はなく、ファイルに格納された shape・dtype をそのまま使う
    (rows, cols の実測値と手入力の img_size が一致している必要はない)。
    header_bytes は .img との戻り値の形を揃えるための互換用で、常に0。
    """
    with Image.open(io.BytesIO(rawdata)) as im:
        img = np.array(im)

    if img.ndim != 2:
        raise ValueError(f"2次元(グレースケール)のTIFF画像のみ対応しています: shape={img.shape}")

    return img, 0
