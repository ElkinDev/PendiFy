import com.google.zxing.BinaryBitmap;
import com.google.zxing.RGBLuminanceSource;
import com.google.zxing.common.HybridBinarizer;
import com.google.zxing.qrcode.QRCodeReader;
import java.awt.image.BufferedImage;
import java.io.BufferedReader;
import java.io.File;
import java.io.InputStreamReader;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import javax.imageio.ImageIO;

/**
 * Reads QR matrices as text from stdin (one row per line, '1' dark and '0' light, a blank line between
 * matrices), draws each at eight pixels a module inside a four-module light border, and prints the text
 * ZXing's QRCodeReader decodes from it, one line per matrix. Run in source-file mode, so nothing is
 * compiled to disk: java -cp core-3.5.4.jar QrMatrixDecode.java
 *
 * <p>With the argument --png it reads rasters instead: one request a line, "path x y width height half", the
 * crop of the PNG at path in its pixels, halved by a 2 by 2 average when half is 1; it prints one line a
 * request, the decoded text, or "NO READ" and the exception's class when ZXing finds no symbol in the crop.
 */
public final class QrMatrixDecode {
  private static final int SCALE = 8;
  private static final int QUIET = 4;

  public static void main(String[] args) throws Exception {
    BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.US_ASCII));
    PrintStream out = new PrintStream(System.out, true, "UTF-8");
    if (args.length == 1 && args[0].equals("--png")) {
      readRasters(in, out);
      return;
    }
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

  private static void readRasters(BufferedReader in, PrintStream out) throws Exception {
    Map<String, BufferedImage> images = new HashMap<>();
    String line;
    while ((line = in.readLine()) != null) {
      line = line.trim();
      if (line.isEmpty()) {
        continue;
      }
      String[] part = line.split(" ");
      if (part.length != 6) {
        throw new IllegalArgumentException("a request is path x y width height half: " + line);
      }
      BufferedImage image = images.get(part[0]);
      if (image == null) {
        image = ImageIO.read(new File(part[0]));
        if (image == null) {
          throw new IllegalArgumentException("not an image ImageIO reads: " + part[0]);
        }
        images.put(part[0], image);
      }
      int x = Integer.parseInt(part[1]);
      int y = Integer.parseInt(part[2]);
      int width = Integer.parseInt(part[3]);
      int height = Integer.parseInt(part[4]);
      int[] pixels = image.getRGB(x, y, width, height, null, 0, width);
      if (part[5].equals("1")) {
        pixels = halved(pixels, width, height);
        width /= 2;
        height /= 2;
      }
      BinaryBitmap bitmap = new BinaryBitmap(new HybridBinarizer(new RGBLuminanceSource(width, height, pixels)));
      try {
        out.println(new QRCodeReader().decode(bitmap).getText());
      } catch (com.google.zxing.ReaderException failure) {
        out.println("NO READ " + failure.getClass().getSimpleName());
      }
    }
  }

  /** Half the width and height, each pixel the average of its 2 by 2 block, channel by channel. */
  private static int[] halved(int[] pixels, int width, int height) {
    int w = width / 2;
    int h = height / 2;
    int[] out = new int[w * h];
    for (int y = 0; y < h; y++) {
      for (int x = 0; x < w; x++) {
        int r = 0;
        int g = 0;
        int b = 0;
        for (int dy = 0; dy < 2; dy++) {
          for (int dx = 0; dx < 2; dx++) {
            int p = pixels[(2 * y + dy) * width + 2 * x + dx];
            r += (p >> 16) & 0xFF;
            g += (p >> 8) & 0xFF;
            b += p & 0xFF;
          }
        }
        out[y * w + x] = 0xFF000000 | ((r / 4) << 16) | ((g / 4) << 8) | (b / 4);
      }
    }
    return out;
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
