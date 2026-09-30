import com.google.zxing.BinaryBitmap;
import com.google.zxing.RGBLuminanceSource;
import com.google.zxing.common.HybridBinarizer;
import com.google.zxing.qrcode.QRCodeReader;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * Reads QR matrices as text from stdin (one row per line, '1' dark and '0' light, a blank line between
 * matrices), draws each at eight pixels a module inside a four-module light border, and prints the text
 * ZXing's QRCodeReader decodes from it, one line per matrix. Run in source-file mode, so nothing is
 * compiled to disk: java -cp core-3.5.4.jar QrMatrixDecode.java
 */
public final class QrMatrixDecode {
  private static final int SCALE = 8;
  private static final int QUIET = 4;

  public static void main(String[] args) throws Exception {
    BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.US_ASCII));
    PrintStream out = new PrintStream(System.out, true, "UTF-8");
    List<String> rows = new ArrayList<>();
    String line;
    while ((line = in.readLine()) != null) {
      line = line.trim();
      if (!line.isEmpty()) {
        rows.add(line);
      } else if (!rows.isEmpty()) {
        out.println(decode(rows));
        rows.clear();
      }
    }
    if (!rows.isEmpty()) {
      out.println(decode(rows));
    }
  }

  private static String decode(List<String> rows) throws Exception {
    int modules = rows.size();
    int side = (modules + 2 * QUIET) * SCALE;
    int[] pixels = new int[side * side];
    Arrays.fill(pixels, 0xFFFFFFFF);
    for (int r = 0; r < modules; r++) {
      String row = rows.get(r);
      if (row.length() != modules) {
        throw new IllegalArgumentException("row " + r + " is not " + modules + " modules wide");
      }
      for (int c = 0; c < modules; c++) {
        if (row.charAt(c) != '1') {
          continue;
        }
        for (int dy = 0; dy < SCALE; dy++) {
          int start = ((r + QUIET) * SCALE + dy) * side + (c + QUIET) * SCALE;
          Arrays.fill(pixels, start, start + SCALE, 0xFF000000);
        }
      }
    }
    BinaryBitmap bitmap = new BinaryBitmap(new HybridBinarizer(new RGBLuminanceSource(side, side, pixels)));
    return new QRCodeReader().decode(bitmap).getText();
  }
}
