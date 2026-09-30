"""QrDecodeTest: a real decode of the encoder's matrices by ZXing, with no gradle.

The jar is the one gradle already cached for the app; the JDK is Android Studio's. Java runs the source
file directly (source-file mode), so nothing is compiled to disk. The phone's camera on the bench stays
the proof of record (design section 4); this case proves the matrix is a QR code a real reader accepts.
"""
import subprocess
import unittest
from pathlib import Path

import support
from support import LINK_ID, SECRET

qr = support.module("qr")

JAR = Path("C:/Users/nikle/.gradle/caches/modules-2/files-2.1/com.google.zxing/core/3.5.4/"
           "955fcd6bcd0723ddfb8ee6ed502d5fdf0e9676a9/core-3.5.4.jar")
JAVA = Path("C:/Program Files/Android/Android Studio/jbr/bin/java.exe")
SOURCE = Path(__file__).resolve().parent / "QrMatrixDecode.java"


def matrix_text(modules):
    return "\n".join("".join("1" if module else "0" for module in row) for row in modules)


def zxing_decode(matrices):
    run = subprocess.run([str(JAVA), "-cp", str(JAR), str(SOURCE)], input="\n\n".join(map(matrix_text, matrices)),
                         capture_output=True, text=True, encoding="utf-8", timeout=120)
    return run.returncode, run.stdout.splitlines(), run.stderr


@unittest.skipUnless(JAR.is_file(), f"the ZXing core 3.5.4 jar is absent from the gradle cache: {JAR}")
@unittest.skipUnless(JAVA.is_file(), f"no JDK at Android Studio's jbr: {JAVA}")
class QrDecodeTest(unittest.TestCase):
    def test_zxing_decodes_the_pairing_address_for_three_secrets(self):
        # Mutation: the zigzag of the data starts downward. Red: ZXing reads scrambled codewords and exits 1.
        # (The format bits drawn without the 0x5412 mask are no mutation for ZXing, which also tries the
        # unmasked word; QrEncodeTest's BCH read-back is the pin of that one.)
        addresses = [qr.pairing_address(secret) for secret in (SECRET, LINK_ID, "HJKM2345NPQR")]
        codes = [qr.encode(address.encode("ascii")) for address in addresses]
        self.assertEqual({code.version for code in codes}, {3})
        status, decoded, errors = zxing_decode([code.modules for code in codes])
        self.assertEqual(status, 0, errors[-1500:])
        self.assertEqual(decoded, addresses)

    def test_zxing_decodes_the_multi_block_versions(self):
        # Mutation: the blocks concatenated instead of interleaved. Red: versions 4 to 6 fail to decode.
        texts = [("v4 " * 21)[:62], ("v5 " * 28)[:84], ("v6 " * 36)[:106]]
        codes = [qr.encode(text.encode("ascii")) for text in texts]
        self.assertEqual([code.version for code in codes], [4, 5, 6])
        status, decoded, errors = zxing_decode([code.modules for code in codes])
        self.assertEqual(status, 0, errors[-1500:])
        self.assertEqual(decoded, texts)


if __name__ == "__main__":
    unittest.main()
