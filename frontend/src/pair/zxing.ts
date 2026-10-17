import { BarcodeDetector, prepareZXingModule } from "barcode-detector/ponyfill";
import wasmUrl from "zxing-wasm/reader/zxing_reader.wasm?url";

// Serve the .wasm from our own origin instead of the default jsDelivr CDN.
prepareZXingModule({
  overrides: {
    locateFile: (path: string, prefix: string) => (path.endsWith(".wasm") ? wasmUrl : prefix + path),
  },
});

// Tag screens are Code 128; products are UPC/EAN, sometimes Code 128/39 SKUs or ITF-14.
export function createDetector(): BarcodeDetector {
  return new BarcodeDetector({
    formats: ["code_128", "code_39", "ean_13", "ean_8", "upc_a", "upc_e", "itf"],
  });
}
